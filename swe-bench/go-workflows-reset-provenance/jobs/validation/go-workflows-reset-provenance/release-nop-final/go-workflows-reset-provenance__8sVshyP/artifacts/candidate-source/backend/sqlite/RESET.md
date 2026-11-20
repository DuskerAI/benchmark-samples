# Reset prototype: supported recovery behavior

This repository includes an original reset prototype on top of go-workflows.
Ordinary execution, inline replay and checkpoint attempt fencing already work.
The reset implementation is incomplete. Repair it without changing existing
method signatures, the event wire format or the dependency manifests.

`ResetWorkflowInstance(ctx, source, newExecutionID, cutSequence)` is a SQLite
method. `source` identifies the currently active execution. The cut is the last
event of a committed workflow task in its logical history. The returned instance
has the same stable instance ID and a fresh execution ID. Its queue, metadata and
parent relationship are preserved. The source is retired without emitting a
business completion to its parent. Follow the returned handle after a reset.

The new execution shares the selected history prefix; it does not copy inherited
history rows. Subsequent events extend the same logical sequence. A reset may
itself be reset, including to a boundary earlier than its inherited prefix.
History reads, suffix reads, task acquisition and cold replay must agree on that
history. Event identity and serialized attributes remain intact. Invalid cuts,
missing/cyclic ancestry and gaps/duplicate sequence positions in a selected view
are errors. Never fall back to running from an incomplete prefix.

A reset is one transaction. A retry with the same source, new execution ID and
cut returns the same handle without changing state, even after later progress.
Reusing that execution ID for a different reset fails without effects. Old workflow
acquisitions cannot publish after reset; acknowledged old checkpoint retries keep
their ordinary read-only behavior. A failed reset leaves the original execution,
lease, history and external-work ownership unchanged.

Externally published work is not undone. Activity/timer/child requests scheduled
in the retained prefix but not settled there still belong to the recovered
workflow. An in-flight activity lease remains usable after adoption. Work already
completed after the cut supplies its recorded result; it is not run again. An
inherited child continues its actual execution and returns to the recovered
parent. A reset child retains its own parent. Work scheduled only in an abandoned
suffix must not satisfy a recovered workflow's new command, even if its numeric
schedule ID is reused. Ignore/drop abandoned child results; reject abandoned
activity completions without consuming replacement work. Identical completion
retries do not create duplicate inputs; conflicting retries fail without effects.

Timer cancellation is a local durable action: cancellation after the selected
cut does not cancel the timer in the recovered prefix. Restore the original
future delivery, including its visibility and attributes. A timer that had already
fired after the cut supplies that delivery rather than a second logical firing.
External activity/child side effects, including cancellation already observed by
such a process, are not reversed or duplicated.

Signals address the stable instance. Reapply signals consumed strictly after the
cut, followed by still-pending signals, without duplication. Preserve the original
serialized input order among the retained post-cut deliveries and the durable
queue order of still-pending inputs. Inputs already in the prefix are not replayed
as new inputs. Execution-addressed cancellation stays execution addressed.

Retained executions pin the ancestry needed to read their histories, including
after they finish. Removing a referenced source fails without partial effects.
After leaves are removed, their unreferenced ancestors can be reclaimed. Bulk
retention must make progress over eligible leaves and then their ancestors, while
keeping dependencies needed by newer retained executions. Removed executions
must not be resurrected by a late result.

Migration 5 introduces the prototype's branch and publication-journal tables and
execution-scoped event keys. The journal retains the original outbound delivery
and any accepted result, independently of queue consumption. Histories written
before this prototype remain readable for ordinary execution. Reset requires
journal coverage for each outstanding retained publication; missing coverage is
an error, not permission to reschedule unknown external work.

Use file-backed or in-memory SQLite. No service, network, administrative clock
change or timing sleep is needed. Keep successful resident execution warm; prefix
sharing must also work after process exit. New helpers/migrations are supported.
