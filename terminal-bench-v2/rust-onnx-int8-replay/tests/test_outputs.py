import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

APP = Path("/app")
BIN = APP / "target" / "release" / "qcert"


def hide_verifier_source_if_requested():
    if os.environ.get("QCERT_HIDE_VERIFIER_SOURCE") != "1":
        return
    here = Path(__file__)
    try:
        here.unlink()
    except FileNotFoundError:
        pass
    shutil.rmtree(here.with_name("__pycache__"), ignore_errors=True)


def build_binary():
    hide_verifier_source_if_requested()
    subprocess.run(["cargo", "build", "--release"], cwd=APP, check=True)
    assert BIN.exists()


def write_case(root, model, requests):
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.qnn").write_text(model, encoding="utf-8")
    rows = []
    for seq, rid, xs, mask in requests:
        rows.append(f"{seq}\t{rid}\t{' '.join(map(str, xs))}\t{' '.join(map(str, mask))}")
    (root / "requests.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def parse_ints(text):
    return [int(x) for x in text.split()]


def parse_floats(text):
    return [float(x) for x in text.split()]


def load_model(case):
    lines = [line.strip() for line in (case / "model.qnn").read_text().splitlines() if line.strip()]
    out = {}
    i = 0
    while i < len(lines):
        parts = lines[i].split(maxsplit=1)
        key = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        if key == "conv_weights":
            rows = []
            for _ in range(out["conv_out"]):
                i += 1
                rows.append(parse_ints(lines[i]))
            out[key] = rows
        elif key == "affine_weights":
            rows = []
            for _ in range(out["classes"]):
                i += 1
                rows.append(parse_ints(lines[i]))
            out[key] = rows
        elif key in {"classes", "input_len", "input_zp", "conv_out", "kernel", "pad_left", "pad_right"}:
            out[key] = int(rest)
        elif key in {"conv_zero_points", "conv_bias", "affine_zero_points", "affine_bias"}:
            out[key] = parse_ints(rest)
        elif key == "affine_scales":
            out[key] = parse_floats(rest)
        elif key == "temperature":
            out[key] = float(rest)
        i += 1
    return out


def load_requests(case):
    reqs = []
    for line in (case / "requests.tsv").read_text().splitlines():
        cols = line.split("\t")
        reqs.append({"seq": int(cols[0]), "request_id": cols[1], "input": parse_ints(cols[2]), "mask": parse_ints(cols[3])})
    return sorted(reqs, key=lambda r: r["seq"])


def reference_records(case):
    model = load_model(case)
    records = []
    for req in load_requests(case):
        out_len = model["input_len"] + model["pad_left"] + model["pad_right"] - model["kernel"] + 1
        features = []
        for oc in range(model["conv_out"]):
            feature = 0
            for pos in range(out_len):
                acc = model["conv_bias"][oc]
                for k in range(model["kernel"]):
                    idx = pos + k - model["pad_left"]
                    if idx < 0 or idx >= model["input_len"] or req["mask"][idx] == 0:
                        xq = model["input_zp"]
                    else:
                        xq = req["input"][idx]
                    acc += (xq - model["input_zp"]) * (model["conv_weights"][oc][k] - model["conv_zero_points"][oc])
                feature += acc
            features.append(feature)
        logits = []
        for c in range(model["classes"]):
            z = model["affine_bias"][c]
            for oc, feat in enumerate(features):
                z += feat * (model["affine_weights"][c][oc] - model["affine_zero_points"][c])
            logits.append(z * model["affine_scales"][c] * model["temperature"])
        top = max(logits)
        exps = [math.exp(v - top) for v in logits]
        denom = sum(exps)
        probs = [v / denom for v in exps]
        best = max(range(len(probs)), key=lambda i: (probs[i], -i))
        ordered = sorted(probs, reverse=True)
        records.append({
            "seq": req["seq"],
            "request_id": req["request_id"],
            "class": best,
            "confidence": probs[best],
            "probabilities": probs,
            "margin": ordered[0] - ordered[1],
        })
    return records


def run_qcert(case, out, *extra):
    subprocess.run([str(BIN), "--case", str(case), "--out", str(out), *map(str, extra)], cwd=APP, check=True)
    pred_path = out / "predictions.jsonl"
    cert_path = out / "cert.json"
    assert pred_path.exists()
    assert cert_path.exists()
    records = [json.loads(line) for line in pred_path.read_text().splitlines() if line]
    cert = json.loads(cert_path.read_text())
    return records, cert, pred_path.read_bytes(), cert_path.read_bytes()


def assert_matches_reference(case, records, cert):
    expected = reference_records(case)
    assert set(cert) == {"records", "classes", "class_counts", "replay_digest"}
    assert [r["seq"] for r in records] == [r["seq"] for r in expected]
    assert [r["request_id"] for r in records] == [r["request_id"] for r in expected]
    for got, exp in zip(records, expected):
        assert set(got) == {"seq", "request_id", "class", "confidence", "probabilities", "margin"}
        assert got["class"] == exp["class"]
        assert len(got["probabilities"]) == len(exp["probabilities"])
        assert all(math.isfinite(v) for v in got["probabilities"])
        assert math.isclose(sum(got["probabilities"]), 1.0, rel_tol=0.0, abs_tol=2e-10)
        assert math.isclose(got["confidence"], got["probabilities"][got["class"]], rel_tol=0.0, abs_tol=5e-12)
        for a, b in zip(got["probabilities"], exp["probabilities"]):
            assert math.isclose(a, b, rel_tol=0.0, abs_tol=8e-10)
        assert math.isclose(got["margin"], exp["margin"], rel_tol=0.0, abs_tol=8e-10)
    counts = [0] * len(expected[0]["probabilities"])
    for rec in records:
        counts[rec["class"]] += 1
    assert cert["records"] == len(records)
    assert cert["classes"] == len(counts)
    assert isinstance(cert["class_counts"], list)
    assert len(cert["class_counts"]) == len(counts)
    assert cert["class_counts"] == counts
    assert isinstance(cert["replay_digest"], str) and re.fullmatch(r"[0-9a-f]{16}", cert["replay_digest"])


def test_certifies_quantized_cases(tmp_path):
    """The certifier emits deterministic probabilities and summaries for generated quantized networks."""
    build_binary()
    case = tmp_path / "case-a"
    model = """classes 4
input_len 7
input_scale 0.03125
input_zp -11
conv_out 3
kernel 3
pad_left 2
pad_right 1
conv_scales 0.02 0.03 0.04
conv_zero_points 3 -5 12
conv_bias 70000 -65000 12345
conv_weights
127 -128 64
-31 87 -90
11 -42 115
affine_scales 0.000002 0.000003 0.0000025 0.0000017
affine_zero_points -7 9 0 13
affine_bias 1200 -900 300 -600
affine_weights
18 -77 31
-46 9 88
127 -12 -65
-91 54 7
temperature 1.0
"""
    requests = [
        (3, "r3", [120, -128, 64, -7, 42, -11, 88], [1, 1, 0, 1, 1, 0, 1]),
        (0, "r0", [-11, -11, -11, -11, -11, -11, -11], [0, 0, 0, 0, 0, 0, 0]),
        (2, "r2", [5, -40, 127, -128, 0, 19, -77], [1, 0, 1, 1, 1, 1, 0]),
        (1, "r1", [33, 18, -91, 74, -12, 55, 6], [1, 1, 1, 0, 1, 1, 1]),
    ]
    write_case(case, model, requests)
    records, cert, _, _ = run_qcert(case, tmp_path / "out-a")
    assert_matches_reference(case, records, cert)


def test_replay_resume_matches_uninterrupted_certification(tmp_path):
    """Checkpointed certification matches the uninterrupted run for a second generated network."""
    build_binary()
    case = tmp_path / "case-b"
    model = """classes 3
input_len 6
input_scale 0.0625
input_zp 7
conv_out 2
kernel 2
pad_left 1
pad_right 0
conv_scales 0.05 0.07
conv_zero_points -13 21
conv_bias 2500 -1700
conv_weights
-128 127
91 -73
affine_scales 0.12 0.09 0.11
affine_zero_points 4 -6 17
affine_bias 0 300 -500
affine_weights
127 -128
-120 111
83 -97
temperature 1.0
"""
    requests = []
    order = [5, 1, 7, 0, 3, 8, 2, 6, 4]
    for pos, seq in enumerate(order):
        xs = [((seq * 37 + j * 29 + pos * 11) % 256) - 128 for j in range(6)]
        mask = [1 if ((seq + j + pos) % 4) else 0 for j in range(6)]
        requests.append((seq, f"q{seq}", xs, mask))
    write_case(case, model, requests)

    full_records, full_cert, full_pred, full_cert_bytes = run_qcert(case, tmp_path / "full")
    assert_matches_reference(case, full_records, full_cert)

    ck1 = tmp_path / "run" / "one.chk"
    ck2 = tmp_path / "run" / "two.chk"
    run_qcert(case, tmp_path / "part1", "--checkpoint", ck1, "--limit", "3")
    run_qcert(case, tmp_path / "part2", "--resume", ck1, "--checkpoint", ck2, "--limit", "2")
    resumed_records, resumed_cert, resumed_pred, resumed_cert_bytes = run_qcert(case, tmp_path / "resumed", "--resume", ck2)

    assert_matches_reference(case, resumed_records, resumed_cert)
    assert resumed_pred == full_pred
    assert resumed_cert_bytes == full_cert_bytes
    extra = tmp_path / "with-junk"
    extra.mkdir()
    (extra / "old.bin").write_bytes(b"stale")
    run_qcert(case, extra)
    assert sorted(p.name for p in extra.iterdir()) == ["cert.json", "predictions.jsonl"]


def test_request_ids_are_preserved_as_json_through_resume(tmp_path):
    """Request identifiers with JSON metacharacters survive sorting, output, digesting, and resume."""
    build_binary()
    case = tmp_path / "case-c"
    model = """classes 3
input_len 5
input_scale 0.5
input_zp 4
conv_out 3
kernel 4
pad_left 3
pad_right 2
conv_scales 0.01 0.02 0.03
conv_zero_points 2 -11 23
conv_bias 90000 -88000 45000
conv_weights
120 -127 33 91
-64 88 -112 7
42 -35 126 -128
affine_scales 0.9 1.1 0.7
affine_zero_points -12 5 19
affine_bias -4000 3000 200
affine_weights
127 -128 64
-91 73 -57
44 18 -123
temperature 3.5
"""
    requests = [
        (9, 'quote"id', [127, -128, 55, 4, 0], [1, 1, 0, 1, 0]),
        (2, r"slash\id", [-10, 4, 99, -77, 31], [0, 1, 1, 0, 1]),
        (5, "plain", [8, 7, 6, 5, 4], [1, 1, 1, 1, 1]),
        (1, "mix-[]{}", [-128, 127, -1, 1, 4], [1, 0, 1, 0, 1]),
    ]
    write_case(case, model, requests)

    full_records, full_cert, full_pred, full_cert_bytes = run_qcert(case, tmp_path / "full-c")
    assert_matches_reference(case, full_records, full_cert)
    assert [r["request_id"] for r in full_records] == ["mix-[]{}", r"slash\id", "plain", 'quote"id']

    ck = tmp_path / "resume" / "ids.chk"
    run_qcert(case, tmp_path / "partial-c", "--checkpoint", ck, "--limit", "2")
    resumed_records, resumed_cert, resumed_pred, resumed_cert_bytes = run_qcert(case, tmp_path / "resumed-c", "--resume", ck)
    assert_matches_reference(case, resumed_records, resumed_cert)
    assert resumed_pred == full_pred
    assert resumed_cert_bytes == full_cert_bytes


def test_extreme_logits_ties_empty_counts_and_zero_limit_resume(tmp_path):
    """Extreme logits stay finite, lower-index ties win, empty classes remain in counts, and zero-limit resume is exact."""
    build_binary()
    case = tmp_path / "case-d"
    model = """classes 5
input_len 4
input_scale 0.125
input_zp -3
conv_out 2
kernel 3
pad_left 2
pad_right 2
conv_scales 0.01 0.02
conv_zero_points 5 -9
conv_bias 200000 -150000
conv_weights
127 -128 64
-31 88 -90
affine_scales 1.0 1.0 1.0 1.0 1.0
affine_zero_points 11 -7 23 0 5
affine_bias 1000 1000 1000 -1000 -2000
affine_weights
11 11
-7 -7
23 23
0 0
5 5
temperature 1.0
"""
    requests = [
        (4, "later", [127, -128, 64, 0], [1, 0, 1, 1]),
        (0, "first", [-3, -3, -3, -3], [0, 0, 0, 0]),
        (2, "middle", [9, -77, 55, -3], [1, 1, 0, 1]),
        (1, "second", [-128, 127, -1, 1], [1, 1, 1, 0]),
    ]
    write_case(case, model, requests)

    full_records, full_cert, full_pred, full_cert_bytes = run_qcert(case, tmp_path / "full-d")
    assert_matches_reference(case, full_records, full_cert)
    assert [rec["class"] for rec in full_records] == [0, 0, 0, 0]
    assert full_cert["class_counts"] == [4, 0, 0, 0, 0]
    for rec in full_records:
        assert all(math.isfinite(v) for v in rec["probabilities"])
        assert math.isclose(rec["probabilities"][0], rec["probabilities"][1], rel_tol=0.0, abs_tol=1e-12)
        assert math.isclose(rec["probabilities"][1], rec["probabilities"][2], rel_tol=0.0, abs_tol=1e-12)
        assert math.isclose(rec["margin"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    ck0 = tmp_path / "chain" / "zero.chk"
    ck1 = tmp_path / "chain" / "one.chk"
    ck2 = tmp_path / "chain" / "two.chk"
    zero_records, zero_cert, _, _ = run_qcert(case, tmp_path / "zero-d", "--checkpoint", ck0, "--limit", "0")
    assert zero_records == []
    assert zero_cert["class_counts"] == [0, 0, 0, 0, 0]
    run_qcert(case, tmp_path / "one-d", "--resume", ck0, "--checkpoint", ck1, "--limit", "1")
    run_qcert(case, tmp_path / "two-d", "--resume", ck1, "--checkpoint", ck2, "--limit", "2")
    resumed_records, resumed_cert, resumed_pred, resumed_cert_bytes = run_qcert(case, tmp_path / "resumed-d", "--resume", ck2)
    assert_matches_reference(case, resumed_records, resumed_cert)
    assert resumed_pred == full_pred
    assert resumed_cert_bytes == full_cert_bytes
