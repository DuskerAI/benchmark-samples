import json
import math
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/qinfer")
BIN = APP / "zig-out/bin/qinfer"


@pytest.fixture(scope="session", autouse=True)
def build_runner():
    """Build the Zig inference runner once for the verifier session."""
    proc = subprocess.run(["zig", "build", "-Doptimize=ReleaseFast"], cwd=APP, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert BIN.exists()


def tensor(shape, data):
    return {"shape": shape, "data": data}


def numel(shape):
    out = 1
    for x in shape:
        out *= x
    return out


def pad4(t, pads, value=0.0):
    shape = t["shape"]
    data = t["data"]
    if len(pads) == 4:
        before = [0, 0, pads[0], pads[1]]
        after = [0, 0, pads[2], pads[3]]
    else:
        before, after = pads[:4], pads[4:]
    out_shape = [shape[i] + before[i] + after[i] for i in range(4)]
    out = [float(value)] * numel(out_shape)
    n0, c0, h0, w0 = shape
    _, _, oh, ow = out_shape
    for n in range(n0):
        for c in range(c0):
            for h in range(h0):
                for w in range(w0):
                    src = (((n * c0 + c) * h0 + h) * w0 + w)
                    dst = (((n * c0 + c) * oh + h + before[2]) * ow + w + before[3])
                    out[dst] = data[src]
    return tensor(out_shape, out)


def qconv(x, w, bias, strides, dilations, pads, xs, xz, ws, wz, ys, yz):
    n0, c0, h0, w0 = x["shape"]
    m0, wc, kh, kw = w["shape"]
    assert c0 == wc
    eff_h = dilations[0] * (kh - 1) + 1
    eff_w = dilations[1] * (kw - 1) + 1
    oh = (h0 + pads[0] + pads[2] - eff_h) // strides[0] + 1
    ow = (w0 + pads[1] + pads[3] - eff_w) // strides[1] + 1
    out = [0.0] * (n0 * m0 * oh * ow)
    for n in range(n0):
        for m in range(m0):
            for yy in range(oh):
                for xx in range(ow):
                    acc = bias[m]
                    for c in range(c0):
                        for ry in range(kh):
                            for rx in range(kw):
                                ih = yy * strides[0] + ry * dilations[0] - pads[0]
                                iw = xx * strides[1] + rx * dilations[1] - pads[1]
                                xv = xz if ih < 0 or iw < 0 or ih >= h0 or iw >= w0 else x["data"][(((n * c0 + c) * h0 + ih) * w0 + iw)]
                                wv = w["data"][(((m * c0 + c) * kh + ry) * kw + rx)]
                                acc += (xv - xz) * xs * (wv - channel_value(wz, m)) * channel_value(ws, m)
                    q = round(acc / ys) + yz
                    q = max(0, min(255, q))
                    out[(((n * m0 + m) * oh + yy) * ow + xx)] = (q - yz) * ys
    return tensor([n0, m0, oh, ow], out)


def channel_value(value, channel):
    if isinstance(value, list):
        return value[channel] if len(value) > 1 else value[0]
    return value


def broadcast_add(a, b):
    ashape, bshape = a["shape"], b["shape"]
    rank = max(len(ashape), len(bshape))
    padded_a = [1] * (rank - len(ashape)) + ashape
    padded_b = [1] * (rank - len(bshape)) + bshape
    out_shape = []
    for da, db in zip(padded_a, padded_b):
        assert da == db or da == 1 or db == 1
        out_shape.append(max(da, db))

    def offset(indices, shape):
        padded = [1] * (rank - len(shape)) + shape
        src = [0 if padded[i] == 1 else indices[i] for i in range(rank)]
        src = src[rank - len(shape):]
        off = 0
        for ix, dim in zip(src, shape):
            off = off * dim + ix
        return off

    out = []
    for flat in range(numel(out_shape)):
        rem = flat
        idx = [0] * rank
        for i in range(rank - 1, -1, -1):
            idx[i] = rem % out_shape[i]
            rem //= out_shape[i]
        out.append(a["data"][offset(idx, ashape)] + b["data"][offset(idx, bshape)])
    return tensor(out_shape, out)


def flatten(x):
    return tensor([x["shape"][0], len(x["data"]) // x["shape"][0]], list(x["data"]))


def gemm(a, b, bias, trans_b=True, alpha=1.0, beta=1.0):
    n, k = a["shape"]
    if trans_b:
        o, bk = b["shape"]
        assert bk == k
    else:
        bk, o = b["shape"]
        assert bk == k
    out = []
    for row in range(n):
        for col in range(o):
            s = 0.0
            for kk in range(k):
                bv = b["data"][col * k + kk] if trans_b else b["data"][kk * o + col]
                s += a["data"][row * k + kk] * bv
            out.append(alpha * s + beta * (bias[0] if len(bias) == 1 else bias[col]))
    return tensor([n, o], out)


def expected_row(model, request):
    tensors = {k: dict(v) for k, v in model["tensors"].items()}
    tensors["input"] = request["input"]
    for layer in model["layers"]:
        op = layer["op"]
        if op == "Pad":
            tensors[layer["output"]] = pad4(tensors[layer["input"]], layer["pads"], layer.get("value", 0.0))
        elif op == "QLinearConv":
            tensors[layer["output"]] = qconv(
                tensors[layer["input"]], tensors[layer["weight"]], tensors[layer["bias"]]["data"],
                layer["strides"], layer["dilations"], layer["pads"], layer["x_scale"], layer["x_zero"],
                layer["w_scale"], layer["w_zero"], layer["y_scale"], layer["y_zero"]
            )
        elif op == "Add":
            tensors[layer["output"]] = broadcast_add(tensors[layer["a"]], tensors[layer["b"]])
        elif op == "Relu":
            x = tensors[layer["input"]]
            tensors[layer["output"]] = tensor(x["shape"], [max(0.0, v) for v in x["data"]])
        elif op == "Flatten":
            tensors[layer["output"]] = flatten(tensors[layer["input"]])
        elif op == "Gemm":
            tensors[layer["output"]] = gemm(tensors[layer["a"]], tensors[layer["b"]], tensors[layer["bias"]]["data"], layer.get("transB", False), layer.get("alpha", 1.0), layer.get("beta", 1.0))
        else:
            raise AssertionError(op)
    scores = tensors[model["output"]]["data"]
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[: model["top_k"]]
    return {"id": request["id"], "scores": scores, "top": [[model["classes"][i], scores[i]] for i in order]}


def run_case(tmp_path, model, requests):
    model_path = tmp_path / "model.json"
    requests_path = tmp_path / "requests.jsonl"
    out_dir = tmp_path / "out"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    requests_path.write_text("\n".join(json.dumps(r) for r in requests) + "\n", encoding="utf-8")
    proc = subprocess.run([str(BIN), "--model", str(model_path), "--requests", str(requests_path), "--out", str(out_dir)], text=True, capture_output=True, check=False)
    return proc, out_dir


def run_case_lines(tmp_path, model, request_lines):
    model_path = tmp_path / "model.json"
    requests_path = tmp_path / "requests.jsonl"
    out_dir = tmp_path / "out"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    requests_path.write_text("\n".join(request_lines) + "\n", encoding="utf-8")
    proc = subprocess.run([str(BIN), "--model", str(model_path), "--requests", str(requests_path), "--out", str(out_dir)], text=True, capture_output=True, check=False)
    return proc, out_dir


def assert_rows_close(actual, expected):
    assert [r["id"] for r in actual] == [r["id"] for r in expected]
    for got, exp in zip(actual, expected):
        assert len(got["scores"]) == len(exp["scores"])
        for a, b in zip(got["scores"], exp["scores"]):
            assert math.isclose(a, b, abs_tol=1e-5)
        assert [x[0] for x in got["top"]] == [x[0] for x in exp["top"]]
        for a, b in zip([x[1] for x in got["top"]], [x[1] for x in exp["top"]]):
            assert math.isclose(a, b, abs_tol=1e-5)


def complex_model():
    conv_weight = [2 + ((i * 7) % 11) for i in range(3 * 2 * 2 * 2)]
    gemm_weight = [((i * 5) % 17 - 8) / 23.0 for i in range(4 * 36)]
    return {
        "classes": ["iris", "moss", "reed", "sedge"],
        "top_k": 3,
        "output": "logits",
        "tensors": {
            "cw": tensor([3, 2, 2, 2], conv_weight),
            "cb": tensor([3], [0.2, -0.1, 0.05]),
            "ab": tensor([1, 3, 1, 1], [0.25, -0.15, 0.05]),
            "gw": tensor([4, 36], gemm_weight),
            "gb": tensor([4], [0.05, -0.12, 0.17, 0.01]),
        },
        "layers": [
            {"op": "Pad", "input": "input", "output": "padded", "pads": [0, 0, 1, 0, 0, 0, 0, 1], "value": 5},
            {"op": "QLinearConv", "input": "padded", "weight": "cw", "bias": "cb", "output": "conv", "strides": [1, 2], "dilations": [2, 1], "pads": [1, 0, 0, 1], "x_scale": 0.2, "x_zero": 5, "w_scale": [0.07, 0.03, 0.11], "w_zero": [4, 7, 6], "y_scale": 0.05, "y_zero": 121},
            {"op": "Add", "a": "conv", "b": "ab", "output": "shifted"},
            {"op": "Relu", "input": "shifted", "output": "active"},
            {"op": "Flatten", "input": "active", "output": "flat"},
            {"op": "Gemm", "a": "flat", "b": "gw", "bias": "gb", "output": "logits", "transB": True, "alpha": 0.75, "beta": 1.0},
        ],
    }


def test_nested_quantized_inference_matches_reference(tmp_path):
    """The full Pad/QLinearConv/Add/Relu/Flatten/Gemm pipeline matches a reference."""
    model = complex_model()
    requests = [
        {"id": "tile-a", "input": tensor([1, 2, 4, 4], [5 + ((i * 3) % 9) - 4 for i in range(32)])},
        {"id": "tile-b", "input": tensor([1, 2, 4, 4], [5 + ((i * 5 + 1) % 13) - 6 for i in range(32)])},
    ]
    proc, out_dir = run_case(tmp_path, model, requests)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in (out_dir / "predictions.jsonl").read_text().splitlines()]
    assert_rows_close(rows, [expected_row(model, r) for r in requests])


def test_scalar_quantized_conv_parameters_match_reference(tmp_path):
    """Scalar QLinearConv weight scale and zero point apply to every output channel."""
    model = {
        "classes": [f"s{i}" for i in range(8)],
        "top_k": 4,
        "output": "flat",
        "tensors": {
            "cw": tensor([2, 1, 2, 2], [4, 8, 11, 5, 9, 3, 7, 12]),
            "cb": tensor([2], [0.1, -0.2]),
        },
        "layers": [
            {"op": "QLinearConv", "input": "input", "weight": "cw", "bias": "cb", "output": "conv", "strides": [1, 1], "dilations": [1, 1], "pads": [0, 0, 0, 0], "x_scale": 0.25, "x_zero": 5, "w_scale": 0.04, "w_zero": 6, "y_scale": 0.05, "y_zero": 120},
            {"op": "Flatten", "input": "conv", "output": "flat"},
        ],
    }
    requests = [{"id": "scalar-qconv", "input": tensor([1, 1, 3, 3], [5, 7, 9, 6, 8, 4, 10, 3, 11])}]
    proc, out_dir = run_case(tmp_path, model, requests)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in (out_dir / "predictions.jsonl").read_text().splitlines()]
    assert_rows_close(rows, [expected_row(model, requests[0])])


def test_four_value_spatial_pad_form_matches_reference(tmp_path):
    """The four-value spatial Pad form participates in inference."""
    model = {
        "classes": [f"c{i}" for i in range(15)],
        "top_k": 5,
        "output": "flat",
        "tensors": {},
        "layers": [
            {"op": "Pad", "input": "input", "output": "padded", "pads": [1, 2, 0, 1], "value": -3.5},
            {"op": "Flatten", "input": "padded", "output": "flat"},
        ],
    }
    requests = [{"id": "spatial-pad", "input": tensor([1, 1, 2, 2], [4.0, -1.0, 7.0, 2.0])}]
    proc, out_dir = run_case(tmp_path, model, requests)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in (out_dir / "predictions.jsonl").read_text().splitlines()]
    assert_rows_close(rows, [expected_row(model, requests[0])])


def test_tied_rankings_keep_class_order(tmp_path):
    """Equal scores keep the class order in the emitted top list."""
    model = {
        "classes": ["ash", "birch", "cedar", "date"],
        "top_k": 4,
        "output": "logits",
        "tensors": {
            "gw": tensor([4, 3], [1, 0, 0, 0, 1, 0, 0, 1, 0, -1, 0, 0]),
            "gb": tensor([4], [0, 0, 0, 0]),
        },
        "layers": [
            {"op": "Flatten", "input": "input", "output": "flat"},
            {"op": "Gemm", "a": "flat", "b": "gw", "bias": "gb", "output": "logits", "transB": True},
        ],
    }
    requests = [{"id": "tie", "input": tensor([1, 1, 1, 3], [2, 2, 1])}]
    proc, out_dir = run_case(tmp_path, model, requests)
    assert proc.returncode == 0, proc.stderr
    row = json.loads((out_dir / "predictions.jsonl").read_text())
    assert row["top"] == [["ash", 2.0], ["birch", 2.0], ["cedar", 2.0], ["date", -2.0]]


def test_malformed_request_rows_are_skipped_in_order(tmp_path):
    """Malformed request rows are skipped while accepted rows stay in stream order."""
    model = {
        "classes": ["north", "east", "south", "west"],
        "top_k": 2,
        "output": "flat",
        "tensors": {},
        "layers": [{"op": "Flatten", "input": "input", "output": "flat"}],
    }
    accepted = [
        {"id": "first", "input": tensor([1, 1, 1, 4], [0.5, 3.0, -1.0, 2.0])},
        {"id": "second", "input": tensor([1, 1, 1, 4], [4.0, -2.0, 1.0, 0.0])},
    ]
    lines = [json.dumps(accepted[0]), "{not-json", json.dumps(accepted[1])]
    proc, out_dir = run_case_lines(tmp_path, model, lines)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in (out_dir / "predictions.jsonl").read_text().splitlines()]
    assert_rows_close(rows, [expected_row(model, r) for r in accepted])


def test_gemm_plain_weight_layout_matches_reference(tmp_path):
    """Gemm reads weights in plain matrix layout when transB is false."""
    model = {
        "classes": ["oak", "elm", "fir", "pine"],
        "top_k": 3,
        "output": "logits",
        "tensors": {
            "gw": tensor([3, 4], [0.5, -0.25, 1.25, 0.75, -1.0, 0.5, 0.0, 1.5, 0.25, 2.0, -0.5, -1.25]),
            "gb": tensor([4], [0.1, -0.2, 0.3, 0.05]),
        },
        "layers": [
            {"op": "Flatten", "input": "input", "output": "flat"},
            {"op": "Gemm", "a": "flat", "b": "gw", "bias": "gb", "output": "logits", "transB": False, "alpha": 1.25, "beta": 0.5},
        ],
    }
    requests = [{"id": "plain-gemm", "input": tensor([1, 1, 1, 3], [2.0, -1.5, 0.75])}]
    proc, out_dir = run_case(tmp_path, model, requests)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in (out_dir / "predictions.jsonl").read_text().splitlines()]
    assert_rows_close(rows, [expected_row(model, requests[0])])


def test_invalid_model_stops_without_predictions(tmp_path):
    """A malformed model exits non-zero and does not leave a prediction stream."""
    model = {
        "classes": ["no", "yes"],
        "top_k": 1,
        "output": "logits",
        "tensors": {"bad": {"shape": [2], "data": [1]}},
        "layers": [{"op": "Flatten", "input": "input", "output": "logits"}],
    }
    requests = [{"id": "bad", "input": tensor([1, 1, 1, 1], [1])}]
    proc, out_dir = run_case(tmp_path, model, requests)
    assert proc.returncode != 0
    assert not (out_dir / "predictions.jsonl").exists()
