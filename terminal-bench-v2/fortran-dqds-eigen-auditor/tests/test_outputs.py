import json
import math
import subprocess
from pathlib import Path


APP = Path("/app/eigen")


def write_case(path, n, scale, diag, offdiag, restart=False):
    path.write_text(
        "\n".join(
            [
                f"n={n}",
                f"scale={scale:.17e}",
                "diag=" + ",".join(f"{x:.17e}" for x in diag),
                "offdiag=" + ",".join(f"{x:.17e}" for x in offdiag),
                f"restart={'true' if restart else 'false'}",
            ]
        )
        + "\n"
    )


def run_case(tmp_path, name, n, scale, diag, offdiag, restart=False, checkpoint=None):
    case_path = tmp_path / f"{name}.in"
    report_path = tmp_path / f"{name}.json"
    checkpoint_path = checkpoint or (tmp_path / f"{name}.chk")
    write_case(case_path, n, scale, diag, offdiag, restart)
    subprocess.run(["make", "clean", "all"], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(
        [str(APP / "eigenaudit"), str(case_path), str(report_path), str(checkpoint_path)],
        cwd=APP,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(report_path.read_text()), checkpoint_path


def matrix_from_case(n, scale, diag, offdiag):
    a = [[0.0 for _ in range(n)] for _ in range(n)]
    for i, value in enumerate(diag):
        a[i][i] = scale * value
    for i, value in enumerate(offdiag):
        a[i][i + 1] = scale * value
        a[i + 1][i] = scale * value
    return a


def reference_eigensystem(a):
    n = len(a)
    b = [row[:] for row in a]
    vecs = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    if n == 1:
        return [b[0][0]], vecs
    for _ in range(50000):
        p, q, maxv = 0, 1, 0.0
        for i in range(n - 1):
            for j in range(i + 1, n):
                if abs(b[i][j]) > maxv:
                    p, q, maxv = i, j, abs(b[i][j])
        scale = max(1.0, max(abs(x) for row in b for x in row))
        if maxv <= 1e-14 * scale:
            break
        app, aqq, apq = b[p][p], b[q][q], b[p][q]
        theta = 0.5 * math.atan2(2.0 * apq, aqq - app)
        c, s = math.cos(theta), math.sin(theta)
        for k in range(n):
            if k != p and k != q:
                bkp, bkq = b[k][p], b[k][q]
                b[k][p] = c * bkp - s * bkq
                b[p][k] = b[k][p]
                b[k][q] = s * bkp + c * bkq
                b[q][k] = b[k][q]
        b[p][p] = c * c * app - 2.0 * s * c * apq + s * s * aqq
        b[q][q] = s * s * app + 2.0 * s * c * apq + c * c * aqq
        b[p][q] = b[q][p] = 0.0
        for k in range(n):
            vkp, vkq = vecs[k][p], vecs[k][q]
            vecs[k][p] = c * vkp - s * vkq
            vecs[k][q] = s * vkp + c * vkq
    pairs = sorted((b[i][i], [vecs[row][i] for row in range(n)]) for i in range(n))
    return [value for value, _ in pairs], [[column[row] for _, column in pairs] for row in range(n)]


def reference_eigenvalues(a):
    return reference_eigensystem(a)[0]


def audit_metrics(a, values, vecs):
    n = len(a)
    norm_a = max(sum(abs(x) for x in row) for row in a)
    residual_max = 0.0
    for i, value in enumerate(values):
        residual = 0.0
        for row in range(n):
            av = sum(a[row][col] * vecs[col][i] for col in range(n))
            residual = max(residual, abs(av - value * vecs[row][i]))
        residual_max = max(residual_max, residual / (max(1.0, norm_a) * max(1.0, abs(value))))

    orthogonality_max = 0.0
    for i in range(n):
        for j in range(n):
            dot = sum(vecs[row][i] * vecs[row][j] for row in range(n))
            if i == j:
                dot -= 1.0
            orthogonality_max = max(orthogonality_max, abs(dot))
    return residual_max, orthogonality_max


def expected_trace_error(scale, diag, values):
    trace_a = sum(scale * value for value in diag)
    return abs(sum(values) - trace_a) / max(1.0, abs(trace_a))


def assert_close_sequence(actual, expected, rtol=3e-10, atol=1e-8):
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        limit = atol + rtol * max(1.0, abs(want))
        assert abs(got - want) <= limit, (got, want, limit)


def test_scaled_cluster_report(tmp_path):
    """The report is accurate for a strongly scaled clustered tridiagonal problem."""
    n = 6
    scale = 1.0e16
    diag = [1.0, 1.0 + 1.0e-10, 2.0, 2.0 + 2.0e-10, 3.0, 3.0 + 3.0e-10]
    offdiag = [2.0e-13, -3.0e-13, 0.0, 4.0e-13, -2.5e-13]
    report, _ = run_case(tmp_path, "cluster", n, scale, diag, offdiag)
    a = matrix_from_case(n, scale, diag, offdiag)
    expected = reference_eigenvalues(a)
    assert report["n"] == n
    assert math.isclose(report["scale"], scale, rel_tol=0.0, abs_tol=0.0)
    assert report["block_count"] == 2
    assert report["eigenvalues"] == sorted(report["eigenvalues"])
    # The absolute allowance is about 2e-11 relative to the 1e16 scale.
    assert_close_sequence(report["eigenvalues"], expected, rtol=2e-11, atol=2e5)
    assert math.isclose(
        report["trace_error"],
        expected_trace_error(scale, diag, report["eigenvalues"]),
        rel_tol=1e-2,
        abs_tol=1e-17,
    )


def test_residual_and_orthogonality_accounting(tmp_path):
    """The numerical error fields are tied to the reported ordered eigensystem."""
    n = 5
    scale = 1.25
    diag = [7.0, -2.0, 5.0, -9.0, 1.0]
    offdiag = [0.0, 0.0, 0.0, 0.0]
    report, _ = run_case(tmp_path, "ordered", n, scale, diag, offdiag)
    a = matrix_from_case(n, scale, diag, offdiag)
    expected = reference_eigenvalues(a)
    assert math.isclose(report["scale"], scale, rel_tol=0.0, abs_tol=0.0)
    assert_close_sequence(report["eigenvalues"], expected, rtol=3e-10, atol=1e-10)
    assert report["residual_max"] <= 1e-12
    assert report["orthogonality_max"] <= 1e-12
    assert math.isclose(
        report["trace_error"],
        expected_trace_error(scale, diag, report["eigenvalues"]),
        rel_tol=1e-2,
        abs_tol=1e-17,
    )


def test_restart_replays_the_same_audit(tmp_path):
    """Checkpoint restart preserves the same scientific audit result."""
    n = 7
    scale = -3.5
    diag = [0.0, 4.0, -1.0, 4.0 + 5e-12, 9.0, 9.0 + 1e-12, 2.0]
    offdiag = [0.25, -0.0, -0.4, 0.05, 0.0, -0.125]
    direct, checkpoint = run_case(tmp_path, "direct", n, scale, diag, offdiag, restart=False)
    replay, _ = run_case(tmp_path, "replay", n, scale, diag, offdiag, restart=True, checkpoint=checkpoint)
    assert math.isclose(direct["scale"], scale, rel_tol=0.0, abs_tol=0.0)
    assert replay == direct


def test_tiny_nonzero_couplings_remain_part_of_blocks(tmp_path):
    """The block summary follows the represented scaled matrix."""
    n = 5
    scale = 1.0e8
    diag = [-1.0, -1.0 + 1e-12, 0.5, 2.0, 2.0 + 1e-12]
    offdiag = [5.0e-13, 0.0, -7.0e-13, -0.0]
    report, _ = run_case(tmp_path, "blocks", n, scale, diag, offdiag)
    expected = reference_eigenvalues(matrix_from_case(n, scale, diag, offdiag))
    assert math.isclose(report["scale"], scale, rel_tol=0.0, abs_tol=0.0)
    assert report["block_count"] == 3
    assert_close_sequence(report["eigenvalues"], expected, rtol=1e-10, atol=1e-7)
