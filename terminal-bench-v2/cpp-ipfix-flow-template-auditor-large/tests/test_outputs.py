import csv
import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

APP = Path(os.environ.get("FLOWGUARD_APP", "/opt/flowguard"))
CAPTURE_ROOT = Path(os.environ.get("FLOWGUARD_CAPTURE_ROOT", "/var/lib/flowguard/captures"))
REGISTRY = Path(os.environ.get("FLOWGUARD_REGISTRY", "/var/lib/flowguard/registry/exporters.tsv"))
OUTPUT = Path(os.environ.get("FLOWGUARD_OUTPUT", "/var/lib/flowguard/output"))
CONFIG = Path(os.environ.get("FLOWGUARD_CONFIG", "/etc/flowguard/collector.toml"))
BIN = APP / "bin" / "flowguard"

PEN = 32473
FIELD_NAMES = ["exporter_id","observation_domain_id","template_id","source_ip","destination_ip","protocol","source_port","destination_port","packets","octets","sampled_packets","sampled_octets","sampler_id","flow_start","flow_end","application_name"]
EXPECTED_BUNDLED_FLOW_SHA256 = "ebefb939fc1eb7c5ab5dc67d8fdae00f0c473f99c13b251d2fa8cfb4f768b5fe"


def run_flowguard(capture_root=CAPTURE_ROOT, registry=REGISTRY, output=OUTPUT):
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True, capture_output=True, text=True)
    subprocess.run([str(BIN), "--config", str(CONFIG), "--capture-root", str(capture_root), "--registry", str(registry), "--output", str(output)], check=True, capture_output=True, text=True)


def load_outputs(output=OUTPUT):
    summary = json.loads((output / "collector_summary.json").read_text())
    flows = [json.loads(line) for line in (output / "flow_records.jsonl").read_text().splitlines() if line.strip()]
    anomalies = [json.loads(line) for line in (output / "anomaly_trace.jsonl").read_text().splitlines() if line.strip()]
    lifecycle = list(csv.DictReader((output / "template_lifecycle.csv").read_text().splitlines()))
    manifest = list(csv.DictReader((output / "audit_manifest.tsv").read_text().splitlines(), delimiter="\t"))
    return summary, flows, anomalies, lifecycle, manifest


def test_build_and_required_outputs_exist():
    """Verify the repaired collector builds, runs, exits successfully, and writes every required artifact."""
    run_flowguard()
    for name in ["collector_summary.json", "template_lifecycle.csv", "flow_records.jsonl", "anomaly_trace.jsonl", "audit_manifest.tsv"]:
        assert (OUTPUT / name).exists(), f"missing {name}"


def test_output_schemas_and_manifest_hashes():
    """Verify output schemas, manifest hashes, and a known-good bundled flow artifact hash."""
    run_flowguard()
    summary, flows, anomalies, lifecycle, manifest = load_outputs()
    assert summary["schema_version"] == 1
    assert isinstance(summary["exporters"], list) and summary["exporters"]
    for row in flows:
        assert list(row.keys()) == FIELD_NAMES
        assert isinstance(row["sampled_packets"], int) and row["sampled_packets"] >= row["packets"]
    assert lifecycle[0].keys() == {"exporter_id", "observation_domain_id", "template_id", "event", "field_count", "message_sequence", "capture_file"}
    for row in anomalies:
        assert {"type", "exporter_id", "observation_domain_id", "sequence", "template_id", "capture_file", "detail"} <= set(row)
    for row in manifest:
        data = (OUTPUT / row["artifact"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
    bundled_flow_hash = hashlib.sha256((OUTPUT / "flow_records.jsonl").read_bytes()).hexdigest()
    assert bundled_flow_hash == EXPECTED_BUNDLED_FLOW_SHA256


def test_template_lifecycle_scoping_and_withdrawal():
    """Verify template state is scoped by exporter/domain and withdrawn templates reject stale data."""
    run_flowguard()
    summary, flows, anomalies, lifecycle, _manifest = load_outputs()
    assert any(r["event"] == "withdraw" and r["template_id"] == "256" and r["exporter_id"] == "exp-alpha" for r in lifecycle)
    assert any(r["event"] == "redefine" and r["template_id"] == "260" and r["exporter_id"] == "exp-red" for r in lifecycle)
    stale = [a for a in anomalies if a["type"] == "stale_template_data"]
    assert stale and stale[0]["exporter_id"] == "exp-alpha"
    # exp-beta reuses template id 256 after exp-alpha withdraws it; this must remain valid.
    assert any(f["exporter_id"] == "exp-beta" and f["template_id"] == 256 for f in flows)
    assert summary["rejected_flows"] == len(stale)


def test_binary_decoding_sampling_and_enterprise_fields():
    """Verify reduced-size counters, options-template sampling, and enterprise applicationName decoding."""
    run_flowguard()
    _, flows, _, _, _ = load_outputs()
    beta = next(f for f in flows if f["exporter_id"] == "exp-beta" and f["source_ip"] == "172.16.1.5")
    assert beta["packets"] == 250
    assert beta["octets"] == 65530
    assert beta["sampled_packets"] == 2500
    assert beta["sampled_octets"] == 655300
    assert beta["application_name"] == "edge-api"
    alpha = next(f for f in flows if f["exporter_id"] == "exp-alpha" and f["source_ip"] == "10.0.0.1")
    assert alpha["sampled_packets"] == 12000
    assert alpha["sampled_octets"] == 800000


def test_sequence_accounting_is_per_scope():
    """Verify sequence gaps are counted per exporter/domain using accepted records from the previous message."""
    run_flowguard()
    summary, _flows, anomalies, _lifecycle, _manifest = load_outputs()
    gaps = [a for a in anomalies if a["type"] == "sequence_gap"]
    assert len(gaps) == 1
    assert gaps[0]["exporter_id"] == "exp-alpha"
    assert gaps[0]["observation_domain_id"] == 100
    assert summary["sequence_gaps"] == 1


def test_flow_records_use_documented_global_sort_order():
    """Verify flow_records.jsonl is sorted by the documented exporter/domain/time/address/port/template key."""
    run_flowguard()
    _, flows, _, _, _ = load_outputs()
    keys = [
        (
            row["exporter_id"],
            row["observation_domain_id"],
            row["flow_start"],
            row["source_ip"],
            row["destination_ip"],
            row["protocol"],
            row["source_port"],
            row["destination_port"],
            row["template_id"],
        )
        for row in flows
    ]
    assert keys == sorted(keys)


def make_dynamic_fixture(base: Path):
    def be16(v): return struct.pack("!H", v)
    def be32(v): return struct.pack("!I", v)
    def be64(v): return struct.pack("!Q", v)
    def ip(s): return bytes(map(int, s.split(".")))
    def fspec(fid, length, ent=False):
        data = be16(fid | (0x8000 if ent else 0)) + be16(length)
        return data + (be32(PEN) if ent else b"")
    def opt_t(tid):
        body = be16(tid) + be16(1) + be16(3) + fspec(48,1) + fspec(34,4) + fspec(35,1)
        return be16(3) + be16(len(body)+4) + body
    def tmpl(tid):
        fields=[(8,4,False),(12,4,False),(4,1,False),(7,2,False),(11,2,False),(2,1,False),(1,2,False),(152,8,False),(153,8,False),(48,1,False),(40001,65535,True)]
        body=be16(tid)+be16(len(fields))+b"".join(fspec(*f) for f in fields)
        return be16(2)+be16(len(body)+4)+body
    def data(tid, recs):
        body = b"".join(recs)
        return be16(tid)+be16(len(body)+4)+body
    def flow(src,dst,packets,octets,app):
        ab = app.encode()
        return ip(src)+ip(dst)+bytes([6])+be16(12345)+be16(443)+bytes([packets])+octets.to_bytes(2,"big")+be64(1700000400000)+be64(1700000400100)+bytes([9])+bytes([len(ab)])+ab
    def opt(interval): return bytes([9])+be32(interval)+bytes([1])
    def msg(seq, sets):
        body = b"".join(sets)
        return be16(10)+be16(len(body)+16)+be32(1700000001)+be32(seq)+be32(900)+body
    cap = base / "dynexp"
    cap.mkdir(parents=True)
    (cap / "0001.ipfix").write_bytes(msg(0, [opt_t(401), data(401, [opt(25)]), tmpl(777), data(777, [flow("192.0.2.77", "203.0.113.77", 200, 4095, "dynamic-app")])]))
    reg = base / "exporters.tsv"
    reg.write_text("capture_dir\texporter_id\tdescription\ndynexp\texp-dynamic\tDynamic verifier exporter\n")
    return cap.parent, reg


def make_layered_dynamic_fixture(base: Path):
    def be16(v): return struct.pack("!H", v)
    def be32(v): return struct.pack("!I", v)
    def be64(v): return struct.pack("!Q", v)
    def ip(s): return bytes(map(int, s.split(".")))
    def fspec(fid, length, ent=False):
        data = be16(fid | (0x8000 if ent else 0)) + be16(length)
        return data + (be32(PEN) if ent else b"")
    def opt_t(tid):
        body = be16(tid) + be16(1) + be16(3) + fspec(48,1) + fspec(34,4) + fspec(35,1)
        return be16(3) + be16(len(body)+4) + body
    def tmpl(tid):
        fields=[(8,4,False),(12,4,False),(4,1,False),(7,2,False),(11,2,False),(2,4,False),(1,8,False),(152,8,False),(153,8,False),(48,1,False),(7233,65535,True)]
        body=be16(tid)+be16(len(fields))+b"".join(fspec(*f) for f in fields)
        return be16(2)+be16(len(body)+4)+body
    def data(tid, recs):
        body = b"".join(recs)
        return be16(tid)+be16(len(body)+4)+body
    def var_text(text):
        raw = text.encode()
        if len(raw) < 255:
            return bytes([len(raw)]) + raw
        return bytes([255]) + be16(len(raw)) + raw
    def flow(src,dst,packets,octets,start,app):
        return ip(src)+ip(dst)+bytes([17])+be16(53000)+be16(2055)+be32(packets)+be64(octets)+be64(start)+be64(start+500)+bytes([4])+var_text(app)
    def opt(interval): return bytes([4])+be32(interval)+bytes([1])
    def msg(seq, sets):
        body = b"".join(sets)
        return be16(10)+be16(len(body)+16)+be32(1700000500)+be32(seq)+be32(901)+body
    cap = base / "nested" / "blue"
    cap.mkdir(parents=True)
    long_app = "analytics-" + ("segment" * 42)
    first = flow("198.51.100.10", "203.0.113.10", 70000, 4294967299, 1700000600000, long_app)
    second = flow("198.51.100.11", "203.0.113.11", 3, 9000000000, 1700000601000, "dns")
    third = flow("198.51.100.12", "203.0.113.12", 5, 17, 1700000602000, "ntp")
    (cap / "0001.ipfix").write_bytes(msg(0, [opt_t(501), data(501, [opt(7)]), tmpl(778), data(778, [first, second])]) + msg(2, [data(778, [third])]))
    reg = base / "exporters.tsv"
    reg.write_text("capture_dir\texporter_id\tdescription\nnested/blue\texp-nested\tNested prefix verifier exporter\n")
    return cap.parent.parent, reg, long_app


def test_dynamic_same_schema_capture_prevents_hardcoding(tmp_path):
    """Generate a fresh capture with different template/exporter ids and verify the collector remains data-driven."""
    capture_root, registry = make_dynamic_fixture(tmp_path / "captures")
    out = tmp_path / "out"
    run_flowguard(capture_root, registry, out)
    summary, flows, anomalies, _lifecycle, _manifest = load_outputs(out)
    assert summary["accepted_flows"] == 1
    assert summary["sampled_packets"] == 5000
    assert summary["sampled_octets"] == 102375
    assert flows[0]["exporter_id"] == "exp-dynamic"
    assert flows[0]["template_id"] == 777
    assert flows[0]["application_name"] == "dynamic-app"
    assert not anomalies


def test_dynamic_layered_capture_exercises_prefix_alias_and_sequence_edges(tmp_path):
    """Generate a nested-prefix capture with multi-record sequence accounting and long enterprise fields."""
    capture_root, registry, long_app = make_layered_dynamic_fixture(tmp_path / "captures")
    out = tmp_path / "out-layered"
    run_flowguard(capture_root, registry, out)
    summary, flows, anomalies, _lifecycle, _manifest = load_outputs(out)
    assert summary["accepted_flows"] == 3
    assert summary["sequence_gaps"] == 0
    assert summary["sampled_packets"] == (70000 + 3 + 5) * 7
    assert summary["sampled_octets"] == (4294967299 + 9000000000 + 17) * 7
    assert {row["exporter_id"] for row in flows} == {"exp-nested"}
    assert [row["template_id"] for row in flows] == [778, 778, 778]
    assert flows[0]["application_name"] == long_app
    assert flows[0]["packets"] == 70000
    assert flows[0]["octets"] == 4294967299
    assert flows[1]["octets"] == 9000000000
    assert all(row["sampler_id"] == 4 for row in flows)
    assert not anomalies
