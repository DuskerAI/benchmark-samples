import threading
import time
import uuid

import pytest
import requests

BASE = "http://127.0.0.1:3000"
TIMEOUT = 10


def uid(prefix: str = "ord") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def create_order(order_id: str | None = None) -> dict:
    oid = order_id or uid()
    r = requests.post(f"{BASE}/orders", json={"orderId": oid}, timeout=TIMEOUT)
    assert r.status_code == 201, r.text
    return r.json()


def add_item(order_id: str, product_id: str, quantity: int = 1, price: float = 10.0) -> dict:
    r = requests.post(
        f"{BASE}/orders/{order_id}/items",
        json={"productId": product_id, "quantity": quantity, "price": price},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    return r.json()


def remove_item(order_id: str, product_id: str) -> dict:
    r = requests.delete(f"{BASE}/orders/{order_id}/items/{product_id}", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    return r.json()


def confirm_order(order_id: str) -> dict:
    r = requests.post(f"{BASE}/orders/{order_id}/confirm", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    return r.json()


def compact(order_id: str) -> dict:
    r = requests.post(f"{BASE}/admin/compact/{order_id}", timeout=TIMEOUT)
    return r.json()


def get_order(order_id: str) -> dict:
    r = requests.get(f"{BASE}/orders/{order_id}", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    return r.json()


def get_snapshot(order_id: str) -> dict | None:
    r = requests.get(f"{BASE}/admin/snapshot/{order_id}", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    return r.json()


def delete_event(order_id: str, version: int) -> None:
    r = requests.delete(f"{BASE}/admin/events/{order_id}/{version}", timeout=TIMEOUT)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------

class TestBasicOperations:
    def test_health(self):
        """Validates that the /health endpoint returns 200 with ok=True."""
        r = requests.get(f"{BASE}/health", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_create_order(self):
        """Validates that creating an order returns 201 with an orderId and version=1."""
        data = create_order()
        assert "orderId" in data
        assert data["version"] == 1

    def test_add_items(self):
        """Validates that adding multiple items correctly accumulates the total and item list."""
        oid = uid()
        create_order(oid)
        add_item(oid, "prod_a", quantity=2, price=15.0)
        add_item(oid, "prod_b", quantity=1, price=30.0)
        state = get_order(oid)
        assert state["total"] == pytest.approx(60.0)
        assert len(state["items"]) == 2

    def test_remove_item(self):
        """Validates that removing an item eliminates it from the order while keeping other items."""
        oid = uid()
        create_order(oid)
        add_item(oid, "keep")
        add_item(oid, "drop", price=99.0)
        remove_item(oid, "drop")
        state = get_order(oid)
        ids = [i["productId"] for i in state["items"]]
        assert "drop" not in ids
        assert "keep" in ids

    def test_confirm_order(self):
        """Validates that confirming an order with items sets its status to 'confirmed'."""
        oid = uid()
        create_order(oid)
        add_item(oid, "x")
        confirm_order(oid)
        state = get_order(oid)
        assert state["status"] == "confirmed"

    def test_confirm_empty_order_rejected(self):
        """Validates that confirming an order with no items returns a 400 error."""
        oid = uid()
        create_order(oid)
        r = requests.post(f"{BASE}/orders/{oid}/confirm", timeout=TIMEOUT)
        assert r.status_code == 400

    def test_basic_compaction_returns_expected_fields(self):
        """Validates that compaction returns success=True with snapshotVersion and eventsCompacted fields."""
        oid = uid()
        create_order(oid)
        add_item(oid, "p1")
        add_item(oid, "p2")
        result = compact(oid)
        assert result.get("success") is True
        assert "snapshotVersion" in result
        assert "eventsCompacted" in result
        assert result["snapshotVersion"] > 0
        assert result["eventsCompacted"] == 3  # create + 2 adds


# ---------------------------------------------------------------------------
# Compaction preserves state
# ---------------------------------------------------------------------------

class TestCompactionPreservesState:
    def test_state_unchanged_after_compaction(self):
        """Validates that order status, total, and items are identical before and after compaction."""
        oid = uid()
        create_order(oid)
        for i in range(10):
            add_item(oid, f"item_{i}", price=float(i + 1))
        for i in range(3):
            remove_item(oid, f"item_{i}")
        confirm_order(oid)

        state_before = get_order(oid)
        result = compact(oid)
        assert result["success"], f"Compaction failed: {result.get('error')}"
        state_after = get_order(oid)

        assert state_after["status"] == state_before["status"], (
            f"Status changed: {state_before['status']} -> {state_after['status']}"
        )
        assert state_after["total"] == pytest.approx(state_before["total"]), (
            f"Total changed: {state_before['total']} -> {state_after['total']}"
        )
        assert sorted(i["productId"] for i in state_after["items"]) == \
               sorted(i["productId"] for i in state_before["items"])

    def test_repeated_compaction_stable(self):
        """Validates that running compaction twice leaves state stable and reports 0 new events compacted on the second run."""
        oid = uid()
        create_order(oid)
        for i in range(5):
            add_item(oid, f"p_{i}", price=float(i + 1))

        r1 = compact(oid)
        assert r1["success"]
        state1 = get_order(oid)

        r2 = compact(oid)
        assert r2["success"]
        assert r2["eventsCompacted"] == 0, "Second compaction should find no new events"
        state2 = get_order(oid)

        assert state1["total"] == pytest.approx(state2["total"])
        assert sorted(i["productId"] for i in state1["items"]) == \
               sorted(i["productId"] for i in state2["items"])

    def test_new_events_after_compaction_reflected(self):
        """Validates that events added after an initial compaction are included in a subsequent compaction."""
        oid = uid()
        create_order(oid)
        for i in range(4):
            add_item(oid, f"batch1_{i}", price=2.0)

        r1 = compact(oid)
        assert r1["success"]

        for i in range(3):
            add_item(oid, f"batch2_{i}", price=5.0)

        r2 = compact(oid)
        assert r2["success"]

        state = get_order(oid)
        all_ids = {i["productId"] for i in state["items"]}
        for i in range(4):
            assert f"batch1_{i}" in all_ids, f"batch1_{i} missing after second compaction"
        for i in range(3):
            assert f"batch2_{i}" in all_ids, f"batch2_{i} missing after second compaction"

    def test_snapshot_version_advances(self):
        """Validates that each successive compaction advances the stored snapshot version."""
        oid = uid()
        create_order(oid)
        add_item(oid, "a")
        add_item(oid, "b")

        r1 = compact(oid)
        assert r1["success"]
        snap1 = get_snapshot(oid)
        assert snap1 is not None
        v1 = snap1["version"]

        add_item(oid, "c")
        add_item(oid, "d")

        r2 = compact(oid)
        assert r2["success"]
        snap2 = get_snapshot(oid)
        v2 = snap2["version"]

        assert v2 > v1, f"Snapshot version should advance: {v1} -> {v2}"


# ---------------------------------------------------------------------------
# Concurrent compaction
# ---------------------------------------------------------------------------

class TestConcurrentCompaction:
    def test_snapshot_never_downgrades(self):
        """Validates that concurrent compactions never downgrade the snapshot, ensuring all items remain visible."""
        oid = uid()
        create_order(oid)
        for i in range(10):
            add_item(oid, f"item_{i}", price=float(i + 1))

        def do_compact():
            compact(oid)

        threads = [threading.Thread(target=do_compact) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # After all concurrent compactions, all 10 items must be visible
        state = get_order(oid)
        all_ids = {i["productId"] for i in state["items"]}
        for i in range(10):
            assert f"item_{i}" in all_ids, (
                f"item_{i} missing — snapshot may have been downgraded\n"
                f"  final items: {sorted(all_ids)}"
            )

    def test_concurrent_compact_and_write(self):
        """Validates that an item written concurrently with compaction is not lost in the final state."""
        oid = uid()
        create_order(oid)
        for i in range(5):
            add_item(oid, f"base_{i}", price=float(i + 1))

        written_ids: list[str] = []
        compact_results: list[dict] = []

        def do_compact():
            compact_results.append(compact(oid))

        def do_write():
            time.sleep(0.003)
            pid = f"racing_{uuid.uuid4().hex[:6]}"
            r = requests.post(
                f"{BASE}/orders/{oid}/items",
                json={"productId": pid, "quantity": 1, "price": 50.0},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                written_ids.append(pid)

        t1 = threading.Thread(target=do_compact)
        t2 = threading.Thread(target=do_write)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        if not written_ids:
            pytest.skip("Write was rejected (conflict) — race window not hit")

        # Compact once more, then verify the written item survived
        compact(oid)
        state = get_order(oid)
        final_ids = {i["productId"] for i in state["items"]}
        missing = [pid for pid in written_ids if pid not in final_ids]
        assert not missing, (
            f"Successfully written items missing from final state: {missing}\n"
            f"  final items: {sorted(final_ids)}"
        )

    def test_concurrent_compaction_all_items_survive(self):
        """Validates that all items survive two concurrent compaction operations followed by a final compaction."""
        oid = uid()
        create_order(oid)
        for i in range(5):
            add_item(oid, f"item_{i}", price=float(i + 1))

        errors: list[str] = []

        def do_compact():
            try:
                compact(oid)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=do_compact)
        t2 = threading.Thread(target=do_compact)
        t1.start()
        time.sleep(0.005)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"Compaction threads raised exceptions: {errors}"

        add_item(oid, "post_concurrent", price=99.0)
        r3 = compact(oid)
        assert r3["success"], f"Post-concurrent compaction failed: {r3.get('error')}"

        state = get_order(oid)
        all_ids = {i["productId"] for i in state["items"]}
        for i in range(5):
            assert f"item_{i}" in all_ids, (
                f"item_{i} missing after concurrent compaction\n  final: {all_ids}"
            )
        assert "post_concurrent" in all_ids


# ---------------------------------------------------------------------------
# Read during compaction
# ---------------------------------------------------------------------------

class TestReadDuringCompaction:
    def test_reads_never_return_stale_state(self):
        """Validates that reads during concurrent compaction never return fewer items than the pre-compaction snapshot."""
        oid = uid()
        create_order(oid)
        expected_item_count = 8
        for i in range(expected_item_count):
            add_item(oid, f"item_{i}", price=float(i + 1))

        # Create initial snapshot, then add more events on top
        compact(oid)
        for i in range(4):
            add_item(oid, f"extra_{i}", price=1.0)

        stale_reads: list[int] = []

        def do_compact():
            for _ in range(4):
                compact(oid)

        def do_read():
            for _ in range(15):
                try:
                    state = get_order(oid)
                    count = len(state["items"])
                    # Must always see at least the snapshot state (8 items)
                    if count < expected_item_count:
                        stale_reads.append(count)
                except Exception:
                    pass
                time.sleep(0.001)

        threads = [
            threading.Thread(target=do_compact),
            threading.Thread(target=do_read),
            threading.Thread(target=do_read),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not stale_reads, (
            f"Reads returned stale state (fewer than {expected_item_count} items): {stale_reads}"
        )

    def test_status_never_regresses_during_compaction(self):
        """Validates that order status and item count remain consistent when reads occur during concurrent compaction."""
        oid = uid()
        create_order(oid)
        for i in range(6):
            add_item(oid, f"item_{i}", price=float(i + 1))
        confirm_order(oid)

        inconsistencies: list[str] = []

        def do_compact():
            for _ in range(5):
                compact(oid)
                time.sleep(0.002)

        def do_read():
            for _ in range(20):
                try:
                    state = get_order(oid)
                    if state["status"] != "confirmed":
                        inconsistencies.append(f"status={state['status']}")
                    if len(state["items"]) != 6:
                        inconsistencies.append(f"item_count={len(state['items'])}")
                except Exception:
                    pass
                time.sleep(0.001)

        threads = [
            threading.Thread(target=do_compact),
            threading.Thread(target=do_read),
            threading.Thread(target=do_read),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not inconsistencies, (
            f"Inconsistent reads observed during compaction: {inconsistencies[:10]}"
        )


# ---------------------------------------------------------------------------
# Version gap detection
# ---------------------------------------------------------------------------

class TestVersionGap:
    def test_gap_at_start_returns_failure(self):
        """Validates that compaction fails with a 'Missing event' error when an early event version is deleted."""
        oid = uid()
        create_order(oid)
        add_item(oid, "a")
        add_item(oid, "b")
        delete_event(oid, 2)

        result = compact(oid)
        assert result["success"] is False, "Compaction should fail when event version 2 is missing"
        assert "Missing event" in (result.get("error") or ""), (
            f"Error must contain 'Missing event', got: {result.get('error')!r}"
        )

    def test_gap_in_middle_returns_failure(self):
        """Validates that compaction fails with a 'Missing event' error when a middle event is deleted."""
        oid = uid()
        create_order(oid)
        for i in range(8):
            add_item(oid, f"item_{i}")
        delete_event(oid, 5)

        result = compact(oid)
        assert result["success"] is False
        assert "Missing event" in (result.get("error") or "")

    def test_no_false_positive_on_clean_sequence(self):
        """Validates that compaction succeeds on a complete, gap-free event sequence."""
        oid = uid()
        create_order(oid)
        for i in range(6):
            add_item(oid, f"p_{i}", price=float(i + 1))

        result = compact(oid)
        assert result["success"] is True, f"Clean compaction should succeed: {result}"
        assert result["eventsCompacted"] == 7  # create + 6 adds

    def test_gap_above_existing_snapshot(self):
        """Validates that compaction fails when a version gap exists in events above an existing snapshot."""
        oid = uid()
        create_order(oid)
        for i in range(4):
            add_item(oid, f"item_{i}")

        r1 = compact(oid)
        assert r1["success"]
        snap_version = r1["snapshotVersion"]

        for i in range(4):
            add_item(oid, f"item2_{i}")

        delete_event(oid, snap_version + 2)

        result = compact(oid)
        assert result["success"] is False, "Compaction should fail on gap above existing snapshot"
        assert "Missing event" in (result.get("error") or "")


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_compact_all_endpoint(self):
        """Validates that the /admin/compact endpoint compacts all aggregates and reports success for each."""
        oids = set(uid() for _ in range(3))
        for oid in oids:
            create_order(oid)
            add_item(oid, "x", price=1.0)

        r = requests.post(f"{BASE}/admin/compact", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 3

        # Only check that the aggregates THIS test created succeeded
        results_by_id = {res["aggregateId"]: res for res in data["results"]}
        for oid in oids:
            assert oid in results_by_id, f"Aggregate {oid} not found in compactAll results"
            res = results_by_id[oid]
            assert res["success"], f"Aggregate {oid} failed to compact: {res.get('error')}"
            assert "snapshotVersion" in res, f"Result for {oid} missing snapshotVersion"
            assert "eventsCompacted" in res, f"Result for {oid} missing eventsCompacted"
            assert res["snapshotVersion"] > 0, f"snapshotVersion for {oid} should be > 0"
            assert res["eventsCompacted"] > 0, f"eventsCompacted for {oid} should be > 0"

    def test_snapshot_version_correct_after_compaction(self):
        """Validates that the stored snapshot version matches the reported snapshotVersion after compaction."""
        oid = uid()
        create_order(oid)
        add_item(oid, "a")
        add_item(oid, "b")
        add_item(oid, "c")

        result = compact(oid)
        assert result["success"]

        snap = get_snapshot(oid)
        assert snap is not None
        assert snap["version"] == result["snapshotVersion"]
        assert snap["version"] == 4, f"Expected v4 (create + 3 adds), got {snap['version']}"

    def test_full_lifecycle_with_compaction(self):
        """Validates a full order lifecycle: creation, compaction, item removal, confirmation, and re-compaction."""
        oid = uid()
        create_order(oid)
        for i in range(5):
            add_item(oid, f"item_{i}", price=float((i + 1) * 10))

        state_pre = get_order(oid)
        r1 = compact(oid)
        assert r1["success"]
        assert r1["eventsCompacted"] == 6  # create + 5 adds

        state_post = get_order(oid)
        assert state_post["total"] == pytest.approx(state_pre["total"])
        assert sorted(i["productId"] for i in state_post["items"]) == \
               sorted(i["productId"] for i in state_pre["items"])

        remove_item(oid, "item_0")
        confirm_order(oid)

        r2 = compact(oid)
        assert r2["success"]
        assert r2["eventsCompacted"] == 2  # remove + confirm

        final = get_order(oid)
        assert final["status"] == "confirmed"
        final_ids = {i["productId"] for i in final["items"]}
        assert "item_0" not in final_ids
        for i in range(1, 5):
            assert f"item_{i}" in final_ids
