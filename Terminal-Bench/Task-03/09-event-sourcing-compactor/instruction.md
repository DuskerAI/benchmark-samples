# Event-Sourcing Compactor

An order management service uses event sourcing: every state change is an immutable event, and current state is built by replaying events. A compaction job snapshots aggregate state and prunes the replayed events to keep the store lean. After deploying this feature last week we're seeing corrupted order state in production: confirmed orders reading back as pending, missing items after compaction runs under load, and compaction jobs that succeed but leave wrong totals. The service runs on port 3000 and source is at `/app/src/`.

Fix the service. The compaction logic is the primary suspect but the read path may also be involved.

## Requirements

- `POST /admin/compact/:id` returns `{"success": true, "snapshotVersion": N, "eventsCompacted": M}` on success, `{"success": false, "error": "..."}` on failure; `snapshotVersion` is the version of the last event folded into the snapshot and `eventsCompacted` is the count of events pruned
- `GET /orders/:id` must always return state consistent with the full event history — before and after compaction, under concurrent writes and concurrent compaction; the response includes `orderId`, `items`, `status`, `total`, and `version`
- Two compactions running simultaneously for the same aggregate must not corrupt state; the higher-versioned snapshot must survive
- A gap in the event version sequence must cause compaction to fail with an error message containing `"Missing event"`
- `POST /admin/compact` (no ID) compacts all aggregates and returns `{"results": [...], "total": N}` where `total` is the number of aggregates processed and each entry in `results` has the shape `{"aggregateId": "...", "success": true/false, "snapshotVersion": N, "eventsCompacted": M}` (same fields as the single-aggregate response)
- `POST /orders/:id/confirm` must return HTTP 400 if the order has no items

## Additional Endpoints

The service exposes the following supporting endpoints used during testing:

- `GET /health` — HTTP 200, returns `{"ok": true}` when the service is running
- `POST /orders` — create a new order; body: `{"orderId": "..."}` (optional); HTTP 201, returns `{"orderId": "...", "version": 1}`
- `POST /orders/:id/items` — add an item; body: `{"productId": "...", "quantity": N, "price": N}`; HTTP 200, returns `{"version": N}`
- `DELETE /orders/:id/items/:productId` — remove an item; HTTP 200, returns `{"version": N}`
- `POST /orders/:id/confirm` — confirm an order; HTTP 200, returns `{"version": N}`
- `GET /orders/:id` — HTTP 200, returns `{"orderId": "...", "items": [...], "status": "...", "total": N, "version": N}`
- `GET /admin/snapshot/:id` — HTTP 200, returns the raw snapshot row for an aggregate or `null` if none exists
- `DELETE /admin/events/:aggregateId/:version` — HTTP 200, deletes a specific event by version (used to simulate gaps in testing)
