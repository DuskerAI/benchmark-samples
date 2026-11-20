import json
import math
import re
import subprocess
from pathlib import Path

BIN = Path("/app/smoother/bin/sensor_smooth")
CANONICAL_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)\.\d{12}\Z")


def raw_number_tokens(raw):
    text = raw.decode("utf-8")
    i = 0
    while i < len(text):
        if text[i] == '"':
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
        elif text[i] == "-" or text[i].isdigit():
            start = i
            i += text[i] == "-"
            if i < len(text) and text[i] == "0":
                i += 1
            else:
                while i < len(text) and text[i].isdigit():
                    i += 1
            if i < len(text) and text[i] == ".":
                i += 1
                while i < len(text) and text[i].isdigit():
                    i += 1
            if i < len(text) and text[i] in "eE":
                i += 1
                if i < len(text) and text[i] in "+-":
                    i += 1
                while i < len(text) and text[i].isdigit():
                    i += 1
            yield text[start:i]
        else:
            i += 1


def assert_canonical_json(raw):
    def ordered_object(pairs):
        keys = [key for key, _ in pairs]
        assert keys == sorted(keys), f"JSON object keys are not lexicographic: {keys}"
        return dict(pairs)

    parsed = json.loads(raw, object_pairs_hook=ordered_object)
    for token in raw_number_tokens(raw):
        assert CANONICAL_NUMBER.fullmatch(token), f"non-canonical numeric token {token}"
        assert token != "-0.000000000000"
    return parsed


def mat_t(a):
    return [list(row) for row in zip(*a)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def mat_vec(a, v):
    return [sum(row[j] * v[j] for j in range(len(v))) for row in a]


def mat_add(a, b):
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def mat_sub(a, b):
    return [[a[i][j] - b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def ident(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def sym(a):
    return [[0.5 * (a[i][j] + a[j][i]) for j in range(len(a))] for i in range(len(a))]


def inv(a):
    if len(a) == 1:
        return [[1.0 / a[0][0]]]
    det = a[0][0] * a[1][1] - a[0][1] * a[1][0]
    return [[a[1][1] / det, -a[0][1] / det], [-a[1][0] / det, a[0][0] / det]]


def det(a):
    return a[0][0] if len(a) == 1 else a[0][0] * a[1][1] - a[0][1] * a[1][0]


def chol2(a):
    a = sym(a)
    l00 = math.sqrt(a[0][0])
    l10 = a[1][0] / l00
    return [[l00, 0.0], [l10, math.sqrt(a[1][1] - l10 * l10)]]


def predict(x, p, dt, q, accel=0.0):
    f = [[1.0, dt], [0.0, 1.0]]
    qq = [[q * dt**4 / 4.0, q * dt**3 / 2.0], [q * dt**3 / 2.0, q * dt**2]]
    xp = mat_vec(f, x)
    xp = [xp[0] + 0.5 * dt * dt * accel, xp[1] + dt * accel]
    return xp, sym(mat_add(mat_mul(mat_mul(f, p), mat_t(f)), qq))


def sensor_model(sensor, z):
    h = sensor["h"]
    if h and isinstance(h[0], list):
        hmat = h
        zvec = z[:]
        r = sensor["r"]
    else:
        hmat = [h]
        zvec = [z]
        r = [[sensor["r"]]]
    bias = sensor.get("bias", [0.0] * len(zvec))
    if not isinstance(bias, list):
        bias = [bias]
    return hmat, r, [zvec[i] - bias[i] for i in range(len(zvec))]


def update_one(x, p, ob, sensor):
    h, r, z = sensor_model(sensor, ob["z"])
    hx = mat_vec(h, x)
    y = [z[i] - hx[i] for i in range(len(z))]
    ht = mat_t(h)
    s = sym(mat_add(mat_mul(mat_mul(h, p), ht), r))
    si = inv(s)
    k = mat_mul(mat_mul(p, ht), si)
    xn = [x[i] + sum(k[i][j] * y[j] for j in range(len(y))) for i in range(2)]
    kh = mat_mul(k, h)
    i_kh = mat_sub(ident(2), kh)
    pn = sym(mat_add(mat_mul(mat_mul(i_kh, p), mat_t(i_kh)), mat_mul(mat_mul(k, r), mat_t(k))))
    ni = sum(y[i] * si[i][j] * y[j] for i in range(len(y)) for j in range(len(y)))
    if "gate" in sensor and ni > sensor["gate"]:
        return x, p, ni, 0.0, 1
    ll = -0.5 * (len(y) * math.log(2.0 * math.pi) + math.log(det(s)) + ni)
    return xn, pn, ni, ll, 0


def reference(cfg):
    dt, q = cfg["dt"], cfg["accel-variance"]
    f = [[1.0, dt], [0.0, 1.0]]
    obs = sorted(enumerate(cfg["observations"]), key=lambda pair: (pair[1]["t"], pair[1]["sensor"], pair[0]))
    controls = cfg.get("controls", [])
    max_t = max([o["t"] for _, o in obs] + [c["t"] for c in controls], default=0)
    by_t = {}
    for _, ob in obs:
        by_t.setdefault(ob["t"], []).append(ob)
    control_by_t = {}
    for control in controls:
        control_by_t[control["t"]] = control_by_t.get(control["t"], 0.0) + control["a"]
    pred_x, pred_p, filt_x, filt_p, inn, rejected = [], [], [], [], [], []
    x = cfg["initial"]["mean"][:]
    p = [row[:] for row in cfg["initial"]["cov"]]
    ll = 0.0
    for t in range(max_t + 1):
        if t:
            x, p = predict(x, p, dt, q, control_by_t.get(t, 0.0))
        pred_x.append(x[:])
        pred_p.append([row[:] for row in p])
        total = 0.0
        rejects = 0
        for ob in by_t.get(t, []):
            x, p, ni, nl, rej = update_one(x, p, ob, cfg["sensors"][ob["sensor"]])
            total += ni
            ll += nl
            rejects += rej
        filt_x.append(x[:])
        filt_p.append([row[:] for row in p])
        inn.append(total)
        rejected.append(rejects)
    smooth_x = [x[:] for x in filt_x]
    smooth_p = [[row[:] for row in p] for p in filt_p]
    for k in range(max_t - 1, -1, -1):
        c = mat_mul(mat_mul(filt_p[k], mat_t(f)), inv(pred_p[k + 1]))
        dx = [smooth_x[k + 1][i] - pred_x[k + 1][i] for i in range(2)]
        smooth_x[k] = [filt_x[k][i] + sum(c[i][j] * dx[j] for j in range(2)) for i in range(2)]
        smooth_p[k] = sym(mat_add(filt_p[k], mat_mul(mat_mul(c, mat_sub(smooth_p[k + 1], pred_p[k + 1])), mat_t(c))))
    return {"run_id": cfg["run-id"], "status": "ok", "log_likelihood": ll,
            "time_steps": [{"t": t, "filtered": filt_x[t], "smoothed": smooth_x[t],
                            "cov_sqrt": chol2(smooth_p[t]), "innovation_norm": inn[t],
                            "rejected_observations": rejected[t]}
                           for t in range(max_t + 1)]}


def edn_value(value):
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, dict):
        return "{" + " ".join(f":{k} {edn_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + " ".join(edn_value(v) for v in value) + "]"
    return str(value)


def edn(cfg):
    sensors = " ".join(f":{name} {edn_value(s)}" for name, s in cfg["sensors"].items())
    observations = " ".join("{" + " ".join(f":{k} {(':'+v) if k == 'sensor' else edn_value(v)}" for k, v in o.items()) + "}" for o in cfg["observations"])
    cov, mean = cfg["initial"]["cov"], cfg["initial"]["mean"]
    controls = ""
    if "controls" in cfg:
        controls = " :controls [" + " ".join("{" + " ".join(f":{k} {edn_value(v)}" for k, v in c.items()) + "}" for c in cfg["controls"]) + "]"
    return (f"{{:run-id \"{cfg['run-id']}\" :dt {cfg['dt']} :accel-variance {cfg['accel-variance']} "
            f":initial {{:mean {edn_value(mean)} :cov {edn_value(cov)}}} "
            f":sensors {{{sensors}}} :observations [{observations}]{controls}}}\n")


def run_case(tmp_path, cfg):
    cfg_path, out_path = tmp_path / "case.edn", tmp_path / "report.json"
    cfg_path.write_text(edn(cfg), encoding="utf-8")
    proc = subprocess.run([str(BIN), str(cfg_path), str(out_path)], text=True, capture_output=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    raw = out_path.read_bytes()
    return assert_canonical_json(raw), raw


def assert_close(actual, expected, tol=4e-8):
    if isinstance(expected, float):
        assert math.isfinite(actual)
        assert abs(actual - expected) <= tol * max(1.0, abs(expected))
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            assert_close(a, e, tol)
    elif isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key in expected:
            assert_close(actual[key], expected[key], tol)
    else:
        assert actual == expected


def assert_cholesky_factor(step):
    factor = step["cov_sqrt"]
    assert len(factor) == 2 and len(factor[0]) == 2 and len(factor[1]) == 2
    assert abs(factor[0][1]) <= 1e-12
    assert factor[0][0] > 0.0 and factor[1][1] > 0.0


def test_reordered_missing_epochs_and_rts_smoothing(tmp_path):
    """A mixed scalar run is sorted by public order, spans missing epochs, and uses backward smoothing."""
    cfg = {"run-id": "field-alpha", "dt": 0.75, "accel-variance": 0.035,
           "initial": {"mean": [1.2, -0.18], "cov": [[0.9, 0.14], [0.14, 0.22]]},
           "sensors": {"range": {"h": [1.0, 0.0], "r": 0.055, "bias": 0.02},
                       "doppler": {"h": [0.15, 1.0], "r": 0.032}},
           "observations": [{"t": 4, "sensor": "range", "z": 1.10}, {"t": 0, "sensor": "range", "z": 1.33},
                            {"t": 2, "sensor": "doppler", "z": -0.21}, {"t": 2, "sensor": "range", "z": 1.02},
                            {"t": 4, "sensor": "doppler", "z": -0.28}]}
    actual, raw1 = run_case(tmp_path, cfg)
    expected = reference(cfg)
    assert_close(actual, expected)
    assert raw1.startswith(b'{"log_likelihood":')
    assert b'"t":0.000000000000' in raw1
    assert actual["time_steps"][1]["innovation_norm"] == 0.0
    assert actual["time_steps"][1]["rejected_observations"] == 0.0
    assert abs(actual["time_steps"][0]["smoothed"][0] - actual["time_steps"][0]["filtered"][0]) > 1e-3
    for step in actual["time_steps"]:
        assert_cholesky_factor(step)
    actual2, raw2 = run_case(tmp_path, cfg)
    assert actual2 == actual
    assert raw2 == raw1

    ordering_cfg = {"run-id": "ordering-gate", "dt": 1.0, "accel-variance": 0.01,
                    "initial": {"mean": [0.0, 0.0], "cov": [[1.0, 0.0], [0.0, 1.0]]},
                    "sensors": {"alpha": {"h": [1.0, 0.0], "r": 0.01},
                                "zeta": {"h": [1.0, 0.0], "r": 0.01, "gate": 1.0}},
                    "observations": [{"t": 0, "sensor": "zeta", "z": 0.1},
                                     {"t": 0, "sensor": "alpha", "z": 0.5}]}
    ordered_actual, _ = run_case(tmp_path, ordering_cfg)
    ordered_expected = reference(ordering_cfg)
    assert_close(ordered_actual, ordered_expected)
    assert ordered_actual["time_steps"][0]["rejected_observations"] == 1.0
    assert ordered_actual["time_steps"][0]["filtered"][0] > 0.49


def test_vector_sensor_with_correlated_noise_and_bias(tmp_path):
    """A coupled two-channel observation uses matrix residual covariance, bias correction, and log determinant terms."""
    cfg = {"run-id": "vector-array", "dt": 0.4, "accel-variance": 0.018,
           "initial": {"mean": [0.7, -0.06], "cov": [[0.82, -0.12], [-0.12, 0.27]]},
           "sensors": {"array": {"h": [[1.0, 0.18], [0.05, 1.0]], "r": [[0.062, 0.014], [0.014, 0.041]], "bias": [0.03, -0.015]},
                       "range": {"h": [1.0, 0.0], "r": 0.07, "bias": -0.02}},
           "observations": [{"t": 3, "sensor": "array", "z": [0.77, -0.12]}, {"t": 0, "sensor": "range", "z": 0.70},
                            {"t": 1, "sensor": "array", "z": [0.73, -0.09]}, {"t": 3, "sensor": "range", "z": 0.79}]}
    actual, _ = run_case(tmp_path, cfg)
    expected = reference(cfg)
    assert_close(actual, expected, tol=7e-8)
    assert actual["time_steps"][1]["innovation_norm"] > 0.0
    assert actual["log_likelihood"] < 1.0

    boundary_cfg = {"run-id": "vector-gate-boundary", "dt": 1.0, "accel-variance": 0.01,
                    "initial": {"mean": [0.0, 0.0], "cov": [[1.0, 0.0], [0.0, 1.0]]},
                    "sensors": {"array": {"h": [[1.0, 0.0], [0.0, 1.0]],
                                           "r": [[1.0, 0.0], [0.0, 1.0]], "gate": 2.0}},
                    "observations": [{"t": 0, "sensor": "array", "z": [2.0, 0.0]}]}
    boundary_actual, _ = run_case(tmp_path, boundary_cfg)
    assert_close(boundary_actual, reference(boundary_cfg))
    assert boundary_actual["time_steps"][0]["innovation_norm"] == 2.0
    assert boundary_actual["time_steps"][0]["rejected_observations"] == 0.0
    assert boundary_actual["time_steps"][0]["filtered"][0] > 0.9

    rejected_cfg = {**boundary_cfg, "run-id": "vector-gate-reject",
                    "observations": [{"t": 0, "sensor": "array", "z": [3.0, 0.0]}]}
    rejected_actual, _ = run_case(tmp_path, rejected_cfg)
    assert_close(rejected_actual, reference(rejected_cfg))
    assert rejected_actual["time_steps"][0]["innovation_norm"] > 2.0
    assert rejected_actual["time_steps"][0]["rejected_observations"] == 1.0
    assert_close(rejected_actual["time_steps"][0]["filtered"], [0.0, 0.0])


def test_generated_duplicate_observations_and_covariance_factor(tmp_path):
    """A generated mixed scalar/vector run rejects fixture hardcoding and checks reported covariance factors."""
    observations = []
    for t in [0, 1, 1, 3, 5, 6, 6, 8]:
        if t in (1, 6):
            observations.append({"t": t, "sensor": "array", "z": [0.4 + 0.13 * t + 0.02 * math.sin(t), 0.08 + 0.011 * t]})
        observations.append({"t": t, "sensor": "bearing" if t % 2 else "range", "z": 0.4 + 0.17 * t + 0.03 * math.sin(t)})
    cfg = {"run-id": "generated-beta", "dt": 0.5, "accel-variance": 0.0125,
           "initial": {"mean": [0.25, 0.08], "cov": [[1.4, -0.21], [-0.21, 0.31]]},
           "sensors": {"range": {"h": [1.0, 0.0], "r": 0.08}, "bearing": {"h": [0.65, 0.22], "r": 0.045},
                       "array": {"h": [[1.0, 0.1], [0.0, 1.0]], "r": [[0.055, 0.006], [0.006, 0.019]], "bias": [0.01, -0.005]}},
           "observations": list(reversed(observations))}
    actual, _ = run_case(tmp_path, cfg)
    expected = reference(cfg)
    assert_close(actual, expected, tol=7e-8)
    for actual_step, expected_step in zip(actual["time_steps"], expected["time_steps"]):
        assert_cholesky_factor(actual_step)
        factor = actual_step["cov_sqrt"]
        reconstructed = [[factor[0][0] ** 2, factor[0][0] * factor[1][0]],
                         [factor[0][0] * factor[1][0], factor[1][0] ** 2 + factor[1][1] ** 2]]
        expected_cov = mat_mul(expected_step["cov_sqrt"], mat_t(expected_step["cov_sqrt"]))
        assert_close(reconstructed, expected_cov, tol=7e-8)


def test_negative_zero_normalization_is_observable(tmp_path):
    """Tiny negative finite values are rounded to canonical positive zero in JSON output."""
    cfg = {"run-id": "negative-zero", "dt": 1.0, "accel-variance": 0.01,
           "initial": {"mean": [-1.0e-13, -1.0e-13], "cov": [[1.0, 0.0], [0.0, 1.0]]},
           "sensors": {"range": {"h": [1.0, 0.0], "r": 0.1}},
           "observations": []}
    actual, raw = run_case(tmp_path, cfg)
    assert_close(actual, reference(cfg))
    assert b'-0.000000000000' not in raw
    assert b'"filtered":[0.000000000000,0.000000000000]' in raw


def test_epoch_controls_and_innovation_gates_change_smoothing_path(tmp_path):
    """Controls and sensor gates interact with prediction, rejected updates, likelihood, and smoothing."""
    cfg = {"run-id": "controlled-gamma", "dt": 0.6, "accel-variance": 0.021,
           "initial": {"mean": [0.15, 0.04], "cov": [[0.72, 0.08], [0.08, 0.18]]},
           "sensors": {"array": {"h": [[1.0, 0.12], [0.04, 1.0]], "r": [[0.045, 0.007], [0.007, 0.033]], "bias": [0.015, -0.01], "gate": 6.0},
                       "range": {"h": [1.0, 0.0], "r": 0.06, "gate": 4.0}},
           "controls": [{"t": 1, "a": 0.08}, {"t": 3, "a": -0.05}, {"t": 3, "a": 0.02}, {"t": 6, "a": 0.06}],
           "observations": [{"t": 0, "sensor": "range", "z": 0.16}, {"t": 2, "sensor": "array", "z": [0.31, 0.10]},
                            {"t": 2, "sensor": "range", "z": 3.4}, {"t": 5, "sensor": "array", "z": [0.52, 0.16]}]}
    actual, _ = run_case(tmp_path, cfg)
    expected = reference(cfg)
    assert_close(actual, expected, tol=8e-8)
    assert len(actual["time_steps"]) == 7
    assert actual["time_steps"][2]["rejected_observations"] == 1.0
    assert actual["time_steps"][2]["innovation_norm"] > 4.0
    assert abs(actual["time_steps"][3]["filtered"][1] - actual["time_steps"][2]["filtered"][1]) > 0.01


def test_interleaved_ordering_vector_gate_and_rejection_counts(tmp_path):
    """Same-epoch ordering, vector gates, controls, and numeric rejection counts interact across smoothing."""
    cfg = {"run-id": "interleaved-delta", "dt": 0.55, "accel-variance": 0.017,
           "initial": {"mean": [0.05, 0.02], "cov": [[0.64, 0.06], [0.06, 0.21]]},
           "sensors": {"alpha": {"h": [1.0, 0.0], "r": 0.035, "gate": 8.0},
                       "middle": {"h": [[1.0, 0.15], [0.05, 1.0]], "r": [[0.04, 0.008], [0.008, 0.025]],
                                  "bias": [0.012, -0.006], "gate": 2.15},
                       "omega": {"h": [0.4, 1.0], "r": 0.028, "bias": -0.004, "gate": 5.0}},
           "controls": [{"t": 1, "a": 0.04}, {"t": 1, "a": -0.01}, {"t": 4, "a": -0.03}],
           "observations": [{"t": 2, "sensor": "omega", "z": 0.14},
                            {"t": 0, "sensor": "omega", "z": 2.5},
                            {"t": 0, "sensor": "middle", "z": [0.08, 0.025]},
                            {"t": 0, "sensor": "alpha", "z": 0.07},
                            {"t": 3, "sensor": "middle", "z": [0.34, 0.11]},
                            {"t": 3, "sensor": "alpha", "z": 1.9}]}
    actual, raw = run_case(tmp_path, cfg)
    expected = reference(cfg)
    assert_close(actual, expected, tol=8e-8)
    assert actual["time_steps"][0]["rejected_observations"] == 1.0
    assert actual["time_steps"][3]["rejected_observations"] == 1.0
    assert isinstance(actual["time_steps"][0]["rejected_observations"], float)
    assert b'"rejected_observations":[1]' not in raw
    assert actual["time_steps"][0]["innovation_norm"] > actual["time_steps"][2]["innovation_norm"]
    assert abs(actual["time_steps"][4]["filtered"][1] - actual["time_steps"][3]["filtered"][1]) > 0.01

    input_order_cfg = {**cfg, "run-id": "input-order-mutant",
                       "observations": [{"t": 0, "sensor": "omega", "z": 2.5},
                                        {"t": 0, "sensor": "middle", "z": [0.08, 0.025]},
                                        {"t": 0, "sensor": "alpha", "z": 0.07}]}
    input_order_actual, _ = run_case(tmp_path, input_order_cfg)
    input_order_expected = reference(input_order_cfg)
    assert_close(input_order_actual, input_order_expected, tol=8e-8)
    assert input_order_actual["time_steps"][0]["rejected_observations"] == 1.0
    assert input_order_actual["time_steps"][0]["filtered"][0] < 0.15


def test_invalid_configs_replace_stale_report(tmp_path):
    """Invalid scalar and vector scientific inputs exit nonzero and replace stale successful reports."""
    bad_inputs = [
        '{:run-id "bad-sensor" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [{:t 0 :sensor :missing :z 1.0}]}',
        '{:run-id "bad-cov" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 2] [2 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "bad-time" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [{:t -1 :sensor :a :z 1.0}]}',
        '{:run-id "nan-dt" :dt ##NaN :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "zero-dt" :dt 0.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "negative-accel-variance" :dt 1.0 :accel-variance -0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "zero-scalar-r" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.0}} :observations [{:t 0 :sensor :a :z 1.0}]}',
        '{:run-id "bad-r" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [[1 0] [0 1]] :r [[0.1 0.3] [0.0 0.1]]}} :observations [{:t 0 :sensor :a :z [1 0]}]}',
        '{:run-id "bias-shape" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [[1 0] [0 1]] :r [[0.1 0] [0 0.1]] :bias [0]}} :observations [{:t 0 :sensor :a :z [1 0]}]}',
        '{:run-id "z-shape" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [[1 0] [0 1]] :r [[0.1 0] [0 0.1]]}} :observations [{:t 0 :sensor :a :z 1.0}]}',
        '{:run-id "extra" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [] :mode :debug}',
        '{:run-id "missing" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}}}',
        '{:run-id "" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "nonsymmetric" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0.2] [0.1 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "bad-h" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [[1 0] [##NaN 1]] :r [[0.1 0] [0 0.1]]}} :observations []}',
        '{:run-id "sensor-extra" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1 :gain 2}} :observations []}',
        '{:run-id "fractional-time" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [{:t 0.5 :sensor :a :z 1.0}]}',
        '{:run-id "control-zero" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [] :controls [{:t 0 :a 0.1}]}',
        '{:run-id "control-nan" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [] :controls [{:t 1 :a ##NaN}]}',
        '{:run-id "control-extra" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [] :controls [{:t 1 :a 0.1 :source :imu}]}',
        '{:run-id "initial-mean-shape" :dt 1.0 :accel-variance 0.1 :initial {:mean [0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "initial-extra" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]] :units :si} :sensors {:a {:h [1 0] :r 0.1}} :observations []}',
        '{:run-id "observation-extra" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [1 0] :r 0.1}} :observations [{:t 0 :sensor :a :z 1.0 :quality 9}]}',
        '{:run-id "sensors-vector" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors [:a {:h [1 0] :r 0.1}] :observations []}',
        '{:run-id "vector-r-not-spd" :dt 1.0 :accel-variance 0.1 :initial {:mean [0 0] :cov [[1 0] [0 1]]} :sensors {:a {:h [[1 0] [0 1]] :r [[0.1 0.2] [0.2 0.1]]}} :observations [{:t 0 :sensor :a :z [1 0]}]}',
    ]
    for index, text in enumerate(bad_inputs):
        cfg_path, out_path = tmp_path / f"bad{index}.edn", tmp_path / f"bad{index}.json"
        cfg_path.write_text(text, encoding="utf-8")
        out_path.write_text('{"status":"ok","time_steps":[1]}', encoding="utf-8")
        proc = subprocess.run([str(BIN), str(cfg_path), str(out_path)], text=True, capture_output=True, timeout=60, check=False)
        assert proc.returncode != 0
        report = assert_canonical_json(out_path.read_bytes())
        assert set(report) == {"error", "run_id", "status"}
        assert report["status"] == "invalid"
        assert isinstance(report["error"], str) and report["error"]
        assert report["run_id"] == json.loads(json.dumps(re.findall(r':run-id "([^"]*)"', text)[0] if ':run-id "' in text else "unknown"))
        assert "time_steps" not in report
    cfg_path = tmp_path / "arity.edn"
    out_path = tmp_path / "arity.json"
    cfg_path.write_text(bad_inputs[0], encoding="utf-8")
    out_path.write_text('{"status":"ok"}', encoding="utf-8")
    proc = subprocess.run([str(BIN), str(cfg_path), str(out_path), "extra"], text=True, capture_output=True, timeout=60, check=False)
    assert proc.returncode != 0
    assert assert_canonical_json(out_path.read_bytes())["status"] == "invalid"
