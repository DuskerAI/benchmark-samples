import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


APP = Path("/app")
TASK = APP / "task_file"
PUBLIC = APP / "public"

EXPECTED_FIXTURE = {
    "sources": [
        {"id": "runtime", "group": "worker", "unit": "us", "origin": 1200000, "offsetNs": 250},
        {"id": "storage", "group": "worker", "unit": "ms", "origin": 1199, "offsetNs": 750},
        {"id": "metrics", "group": "process", "unit": "s", "origin": 1, "offsetNs": 1999999500},
    ],
    "items": [
        {"id": "a1", "kind": "span", "source": "runtime", "lane": "main", "label": "parse", "category": "cpu", "ts": 300, "dur": 120},
        {"id": "a2", "kind": "span", "source": "runtime", "lane": "main", "label": "decode", "category": "io", "ts": 360, "dur": 90},
        {
            "id": "b1",
            "kind": "span",
            "source": "storage",
            "lane": "main",
            "label": "flush",
            "category": "io",
            "ts": 1500,
            "dur": 700,
            "unit": "us",
        },
        {"id": "a3", "kind": "event", "source": "runtime", "lane": "main", "label": "checkpoint", "category": "state", "ts": 510},
        {
            "id": "m1",
            "kind": "counter",
            "source": "metrics",
            "lane": "rss",
            "label": "rss",
            "category": "memory",
            "ts": 200,
            "unit": "ms",
            "value": 41.5,
        },
        {
            "id": "m2",
            "kind": "counter",
            "source": "metrics",
            "lane": "rss",
            "label": "rss",
            "category": "memory",
            "ts": 230,
            "unit": "ms",
            "value": 39.25,
        },
        {"id": "a4", "kind": "span", "source": "runtime", "lane": "aux", "label": "publish", "category": "network", "ts": 470, "dur": 35},
    ],
}


def run_checked(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def unit_scale(unit):
    return {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}[unit]


def reference(bundle):
    sources = {source["id"]: source for source in bundle["sources"]}
    groups = {}
    start_ns = None
    end_ns = None
    for item in bundle["items"]:
        source = sources[item["source"]]
        unit = item.get("unit", source["unit"])
        start = source.get("origin", 0) * unit_scale(source["unit"]) + source.get("offsetNs", 0) + item["ts"] * unit_scale(unit)
        end = start + item.get("dur", 0) * unit_scale(unit) if item["kind"] == "span" else start
        start_ns = start if start_ns is None else min(start_ns, start)
        end_ns = end if end_ns is None else max(end_ns, end)
        kind = "counter" if item["kind"] == "counter" else "activity"
        key = f"{source['group']}/{item['lane']}"
        groups.setdefault((key, kind, source["group"], item["lane"]), []).append({
            "id": item["id"],
            "kind": item["kind"],
            "label": item["label"],
            "category": item["category"],
            "source": source["id"],
            "startNs": start,
            "endNs": end,
            "value": item.get("value"),
        })
    lanes = []
    for key_tuple in sorted(groups):
        key, kind, group, lane_name = key_tuple
        items = sorted(groups[key_tuple], key=lambda x: (x["startNs"], x["endNs"], x["id"]))
        occupied = []
        max_depth = 0
        for item in items:
            if item["kind"] != "span":
                item["depth"] = 0
                continue
            depth = 0
            while depth < len(occupied) and occupied[depth] > item["startNs"]:
                depth += 1
            if depth == len(occupied):
                occupied.append(item["endNs"])
            else:
                occupied[depth] = item["endNs"]
            item["depth"] = depth
            max_depth = max(max_depth, depth + 1)
        lanes.append({"key": key, "kind": kind, "group": group, "lane": lane_name, "maxDepth": max_depth, "items": items})
    return {"version": 1, "range": {"startNs": start_ns or 0, "endNs": end_ns or 0}, "lanes": lanes}


def build_outputs(input_path, out_dir):
    run_checked(["cargo", "build", "--locked"], cwd=TASK)
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline = out_dir / "timeline.json"
    report = out_dir / "report.json"
    run_checked([str(TASK / "target/debug/tracepack"), str(input_path), str(timeline), str(report)], cwd=TASK)
    return json.loads(timeline.read_text()), json.loads(report.read_text())


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def assert_timeline_matches(timeline, expected):
    assert timeline["version"] == 1
    assert timeline["range"] == expected["range"]
    got_lanes = [(lane["key"], lane["kind"], lane["group"], lane["lane"]) for lane in timeline["lanes"]]
    exp_lanes = [(lane["key"], lane["kind"], lane["group"], lane["lane"]) for lane in expected["lanes"]]
    assert got_lanes == exp_lanes
    for got, exp in zip(timeline["lanes"], expected["lanes"]):
        assert got["maxDepth"] == exp["maxDepth"]
        assert [(i["id"], i["kind"], i["label"], i["category"], i["source"], i["startNs"], i["endNs"], i["depth"], i.get("value")) for i in got["items"]] == [
            (i["id"], i["kind"], i["label"], i["category"], i["source"], i["startNs"], i["endNs"], i["depth"], i.get("value")) for i in exp["items"]
        ]
    indexed = sorted(timeline["index"])
    listed = sorted(item["id"] for lane in expected["lanes"] for item in lane["items"])
    assert indexed == listed
    for lane in expected["lanes"]:
        for ordinal, item in enumerate(lane["items"]):
            assert timeline["index"][item["id"]] == {"lane": lane["key"], "kind": lane["kind"], "ordinal": ordinal}


def test_processor_outputs_consistent_bundle(tmp_path):
    """The bundled trace is normalized without changing the fixture file."""
    bundle_path = TASK / "fixtures/session.json"
    bundle = json.loads(bundle_path.read_text())
    assert bundle == EXPECTED_FIXTURE
    timeline, report = build_outputs(bundle_path, tmp_path)
    expected = reference(bundle)
    assert_timeline_matches(timeline, expected)
    assert report["range"] == expected["range"]
    got_lane_report = [(row["key"], row["kind"], row["count"], row["firstNs"], row["lastNs"]) for row in report["lanes"]]
    assert got_lane_report == [
        (
            lane["key"],
            lane["kind"],
            len(lane["items"]),
            min(item["startNs"] for item in lane["items"]),
            max(max(item["startNs"], item["endNs"]) for item in lane["items"]),
        )
        for lane in expected["lanes"]
    ]
    counter_rows = {row["key"]: row for row in report["counters"]}
    for lane in expected["lanes"]:
        if lane["kind"] == "counter":
            values = [item["value"] for item in lane["items"]]
            assert counter_rows[lane["key"]] == {"key": lane["key"], "min": min(values), "max": max(values), "last": values[-1]}


def test_processor_accepts_same_shape_input(tmp_path):
    """A second same-format bundle produces the same schema and ordering rules."""
    bundle = {
        "sources": [
            {"id": "alpha", "group": "g", "unit": "ms", "origin": 2, "offsetNs": 50},
            {"id": "beta", "group": "g", "unit": "us", "origin": 2000, "offsetNs": 50},
            {"id": "gamma", "group": "m", "unit": "ns", "origin": 3_000_000_000},
        ],
        "items": [
            {"id": "x2", "kind": "span", "source": "beta", "lane": "work", "label": "merge", "category": "b", "ts": 300, "dur": 200},
            {"id": "x1", "kind": "span", "source": "alpha", "lane": "work", "label": "open", "category": "a", "ts": 0, "dur": 800, "unit": "us"},
            {"id": "x3", "kind": "event", "source": "alpha", "lane": "work", "label": "point", "category": "a", "ts": 1200, "unit": "us"},
            {"id": "x4", "kind": "counter", "source": "gamma", "lane": "q", "label": "q", "category": "metric", "ts": 4_000_000, "value": 7.0},
            {"id": "x5", "kind": "counter", "source": "gamma", "lane": "q", "label": "q", "category": "metric", "ts": 5_000_000, "value": 5.5},
        ],
    }
    input_path = tmp_path / "bundle.json"
    input_path.write_text(json.dumps(bundle))
    timeline, report = build_outputs(input_path, tmp_path / "out")
    expected = reference(bundle)
    assert_timeline_matches(timeline, expected)
    assert report["lanes"][0]["key"] == expected["lanes"][0]["key"]
    assert report["counters"][0]["last"] == expected["lanes"][-1]["items"][-1]["value"]


def test_processor_handles_ties_escapes_and_reused_rows(tmp_path):
    """A generated bundle exercises JSON escapes, tie ordering, row reuse, and merged sources."""
    bundle = {
        "sources": [
            {"id": "clock", "group": "zeta", "unit": "ns", "origin": 0},
            {"id": "remote", "group": "zeta", "unit": "us", "origin": 1, "offsetNs": 5},
            {"id": "meter", "group": "alpha", "unit": "ms", "origin": 2},
        ],
        "items": [
            {"id": "tie-b", "kind": "span", "source": "clock", "lane": "work", "label": "β-line", "category": "cpu", "ts": 100, "dur": 20},
            {"id": "wide", "kind": "span", "source": "clock", "lane": "work", "label": "wide", "category": "cpu", "ts": 90, "dur": 50},
            {"id": "tie-a", "kind": "event", "source": "clock", "lane": "work", "label": "quote \"event\"", "category": "state", "ts": 100},
            {"id": "reuse", "kind": "span", "source": "clock", "lane": "work", "label": "row-reuse", "category": "cpu", "ts": 140, "dur": 10},
            {"id": "remote-ns", "kind": "event", "source": "remote", "lane": "work", "label": "remote", "category": "io", "ts": 7, "unit": "ns"},
            {"id": "c-high", "kind": "counter", "source": "meter", "lane": "rss", "label": "rss", "category": "memory", "ts": 3, "value": 12.5},
            {"id": "c-low", "kind": "counter", "source": "meter", "lane": "rss", "label": "rss", "category": "memory", "ts": 1, "value": 8.0},
        ],
    }
    input_path = tmp_path / "escaped.json"
    input_path.write_text(json.dumps(bundle))
    timeline, report = build_outputs(input_path, tmp_path / "out")
    expected = reference(bundle)
    assert_timeline_matches(timeline, expected)
    activity = next(lane for lane in timeline["lanes"] if lane["key"] == "zeta/work" and lane["kind"] == "activity")
    assert [(item["id"], item["depth"]) for item in activity["items"]] == [("wide", 0), ("tie-a", 0), ("tie-b", 1), ("reuse", 0), ("remote-ns", 0)]
    assert next(row for row in report["counters"] if row["key"] == "alpha/rss") == {"key": "alpha/rss", "min": 8.0, "max": 12.5, "last": 12.5}


def test_processor_rejects_invalid_bundle_shapes(tmp_path):
    """Invalid JSON, missing sources, and unsupported units fail without partial success."""
    run_checked(["cargo", "build", "--locked"], cwd=TASK)
    cases = [
        "{ not json",
        json.dumps({"sources": [], "items": [{"id": "bad", "kind": "event", "source": "missing", "lane": "x", "label": "bad", "category": "bad", "ts": 1}]}),
        json.dumps({"sources": [{"id": "s", "group": "g", "unit": "fortnight"}], "items": [{"id": "bad", "kind": "event", "source": "s", "lane": "x", "label": "bad", "category": "bad", "ts": 1}]}),
    ]
    for idx, text in enumerate(cases):
        input_path = tmp_path / f"bad-{idx}.json"
        timeline = tmp_path / f"bad-{idx}-timeline.json"
        report = tmp_path / f"bad-{idx}-report.json"
        input_path.write_text(text)
        result = subprocess.run([str(TASK / "target/debug/tracepack"), str(input_path), str(timeline), str(report)], cwd=TASK, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode != 0


def test_browser_interaction_roundtrip(tmp_path):
    """Verify filter, select, bookmark, active-window, reset, and export behavior."""
    data_dir = PUBLIC / "data"
    build_outputs(TASK / "fixtures/session.json", data_dir)
    run_checked(["npm", "run", "build"], cwd=APP)
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=PUBLIC,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.5)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1100, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            expect(page.locator("#app-title")).to_have_text("Trace Timeline Viewer")
            data = json.loads((data_dir / "timeline.json").read_text())
            all_items = [item for lane in data["lanes"] for item in lane["items"]]
            lane_by_id = {item["id"]: lane for lane in data["lanes"] for item in lane["items"]}
            initial_export = json.loads(page.locator("#export").input_value())
            initial_dom_ids = page.locator(".entry, .sample").evaluate_all("els => els.map((el) => el.dataset.id)")
            assert initial_export["visibleIds"] == initial_dom_ids
            target = sorted((item for item in all_items if item["category"] == "io"), key=lambda item: item["startNs"])[0]
            target_lane = lane_by_id[target["id"]]
            filter_queries = [
                target["id"],
                target["label"],
                target["category"],
                target["source"],
                target["kind"],
                target_lane["key"],
            ]
            for query in filter_queries:
                page.fill("#filter", query)
                expect(page.locator(f"[data-id='{target['id']}']")).to_have_count(1)
            page.fill("#filter", target["source"])
            expect(page.locator(f"[data-id='{target['id']}']")).to_have_count(1)
            page.locator(f"[data-id='{target['id']}']").click()
            expect(page.locator("#details")).to_contain_text(target["id"])
            page.click("#bookmark")
            expect(page.locator(f"[data-id='{target['id']}']")).to_have_count(1)
            second = next(item for item in all_items if item["id"] != target["id"])
            page.fill("#filter", "")
            page.locator(f"[data-id='{second['id']}']").click()
            page.click("#bookmark")
            assert json.loads(page.locator("#export").input_value())["bookmarks"] == sorted([target["id"], second["id"]])
            expect(page.locator("#bookmarks [data-id]")).to_have_count(0)
            page.click("#bookmark")
            assert json.loads(page.locator("#export").input_value())["bookmarks"] == [target["id"]]
            page.fill("#filter", "memory")
            page.locator("#bookmarks button", has_text=target["id"]).click()
            expect(page.locator("#details")).to_contain_text(target["id"])
            filtered_export = json.loads(page.locator("#export").input_value())
            assert filtered_export["filter"] == "memory"
            page.fill("#filter", "")
            start = target["startNs"] - 10_000
            end = target["endNs"] + 10_000
            page.fill("#from-ns", str(start))
            page.fill("#to-ns", str(end))
            page.click("#apply-window")
            box = page.locator(f"[data-id='{target['id']}']").bounding_box()
            rail = page.locator(f".lane:has([data-id='{target['id']}']) .rail").bounding_box()
            assert box and rail
            assert rail["x"] <= box["x"] <= rail["x"] + rail["width"] * 0.25
            exported = json.loads(page.locator("#export").input_value())
            assert exported["selectedId"] == target["id"]
            assert exported["bookmarks"] == [target["id"]]
            assert exported["window"] == {"startNs": start, "endNs": end}
            assert exported["filter"] == ""
            assert target["id"] in exported["visibleIds"]
            window_dom_ids = page.locator(".entry, .sample").evaluate_all("els => els.map((el) => el.dataset.id)")
            assert exported["visibleIds"] == window_dom_ids
            page.click("#reset-window")
            reset_export = json.loads(page.locator("#export").input_value())
            assert reset_export["window"] == data["range"]
            assert reset_export["filter"] == ""
            assert page.locator("#from-ns").input_value() == str(data["range"]["startNs"])
            assert page.locator("#to-ns").input_value() == str(data["range"]["endNs"])
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
