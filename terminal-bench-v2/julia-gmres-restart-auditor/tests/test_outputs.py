import json
import math
import shutil
import subprocess
from pathlib import Path

APP = Path("/app/gmresaudit")
CMD = ["julia", f"--project={APP}", str(APP / "audit.jl")]


def write_case(
    root,
    matrix_text,
    rhs,
    precond=None,
    restart=2,
    maxiter=10,
    tol=1e-10,
    true_tol=1e-9,
):
    """Create a GMRES audit case using the public case-file schema."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "A.mtx").write_text(matrix_text)
    (root / "b.txt").write_text("\n".join(str(x) for x in rhs) + "\n")
    lines = [
        "matrix = A.mtx",
        "rhs = b.txt",
        f"restart = {restart}",
        f"maxiter = {maxiter}",
        f"tol = {tol}",
        f"true_tol = {true_tol}",
    ]
    if precond is not None:
        (root / "m.txt").write_text("\n".join(str(x) for x in precond) + "\n")
        lines.insert(2, "preconditioner = m.txt")
    (root / "case.txt").write_text("\n".join(lines) + "\n")


def run_case(case_dir, report):
    """Run the public Julia command and return the completed process."""
    return subprocess.run(
        CMD + [str(case_dir), str(report)],
        check=False,
        text=True,
        capture_output=True,
        timeout=90,
    )


def assert_invalid_case(case_dir, report):
    """Invalid public-contract cases fail and remove an existing report."""
    report.write_text("stale")
    proc = run_case(case_dir, report)
    assert proc.returncode != 0
    assert not report.exists()


def matvec(a, x):
    return [sum(row[j] * x[j] for j in range(len(x))) for row in a]


def dot(x, y):
    return sum(xi * yi for xi, yi in zip(x, y))


def norm(x):
    return math.sqrt(dot(x, x))


def solve_linear(a, b):
    """Solve a small dense least-squares normal equation system."""
    m, n = len(a), len(a[0])
    ata = [
        [sum(a[i][r] * a[i][c] for i in range(m)) for c in range(n)] for r in range(n)
    ]
    atb = [sum(a[i][r] * b[i] for i in range(m)) for r in range(n)]
    for p in range(n):
        pivot = max(range(p, n), key=lambda r: abs(ata[r][p]))
        ata[p], ata[pivot] = ata[pivot], ata[p]
        atb[p], atb[pivot] = atb[pivot], atb[p]
        div = ata[p][p]
        assert abs(div) > 1e-18
        for c in range(p, n):
            ata[p][c] /= div
        atb[p] /= div
        for r in range(n):
            if r == p:
                continue
            f = ata[r][p]
            for c in range(p, n):
                ata[r][c] -= f * ata[p][c]
            atb[r] -= f * atb[p]
    return atb


def ref_gmres(a, b, mdiag, restart, maxiter, tol, true_tol):
    """Compute the independent left-preconditioned restarted GMRES audit."""
    n = len(b)
    x = [0.0] * n
    records = []
    total = 0
    while total < maxiter:
        r = [bi - ai for bi, ai in zip(b, matvec(a, x))]
        z = [ri / mi for ri, mi in zip(r, mdiag)]
        beta = norm(z)
        if beta == 0:
            break
        kmax = min(restart, maxiter - total)
        v = [[zi / beta for zi in z]]
        h = [[0.0 for _ in range(kmax)] for _ in range(kmax + 1)]
        used = 0
        for j in range(kmax):
            w = [wi / mi for wi, mi in zip(matvec(a, v[j]), mdiag)]
            for i in range(j + 1):
                h[i][j] = dot(v[i], w)
                w = [wt - h[i][j] * vi for wt, vi in zip(w, v[i])]
            for i in range(j + 1):
                hc = dot(v[i], w)
                h[i][j] += hc
                w = [wt - hc * vi for wt, vi in zip(w, v[i])]
            h[j + 1][j] = norm(w)
            used = j + 1
            if h[j + 1][j] <= 1e-13:
                break
            v.append([wi / h[j + 1][j] for wi in w])
        accepted = x
        for k in range(1, used + 1):
            hk = [[h[row][col] for col in range(k)] for row in range(k + 1)]
            e = [beta] + [0.0] * k
            y = solve_linear(hk, e)
            xt = [x[i] + sum(v[col][i] * y[col] for col in range(k)) for i in range(n)]
            rt = [bi - ai for bi, ai in zip(b, matvec(a, xt))]
            pr = norm([ri / mi for ri, mi in zip(rt, mdiag)])
            tr = norm(rt)
            total += 1
            records.append(
                (
                    total,
                    ((total - 1) // restart) + 1,
                    ((total - 1) % restart) + 1,
                    pr,
                    tr,
                )
            )
            accepted = xt
            if pr <= tol and tr <= true_tol:
                return xt, records, "converged", pr, tr
            if total >= maxiter:
                return xt, records, "not_converged", pr, tr
        x = accepted
    rt = [bi - ai for bi, ai in zip(b, matvec(a, x))]
    return (
        x,
        records,
        "not_converged",
        norm([ri / mi for ri, mi in zip(rt, mdiag)]),
        norm(rt),
    )


def assert_report_close(report, expected):
    """Compare report semantics to an independent GMRES reference."""
    x, records, status, pr, tr = expected
    assert report["status"] == status
    assert report["iterations"] == len(records)
    assert math.isclose(
        report["final_preconditioned_residual"], pr, rel_tol=2e-7, abs_tol=2e-9
    )
    assert math.isclose(report["final_true_residual"], tr, rel_tol=2e-7, abs_tol=2e-9)
    assert len(report["solution"]) == len(x)
    for got, want in zip(report["solution"], x):
        assert math.isclose(got, want, rel_tol=2e-7, abs_tol=2e-8)
    assert len(report["records"]) == len(records)
    for got, want in zip(report["records"], records):
        it, cyc, cit, want_pr, want_tr = want
        assert got["iteration"] == it
        assert got["cycle"] == cyc
        assert got["cycle_iteration"] == cit
        assert math.isfinite(got["orthogonality_error"])
        assert got["orthogonality_error"] <= 1e-8
        assert math.isclose(
            got["preconditioned_residual"], want_pr, rel_tol=2e-7, abs_tol=2e-9
        )
        assert math.isclose(got["true_residual"], want_tr, rel_tol=2e-7, abs_tol=2e-9)


def test_symmetric_matrix_market_and_restart_records(tmp_path):
    """Symmetric Matrix Market input produces the reference restart audit."""
    case = tmp_path / "sym_restart"
    matrix = """%%MatrixMarket matrix coordinate real symmetric
% comments may appear before dimensions
4 4 7
1 1 5.0
2 1 -1.0
2 2 4.0
3 2 0.75
3 3 3.5
4 3 -0.5
4 4 2.75
"""
    rhs = [2.0, -1.0, 0.5, 3.0]
    pre = [5.0, 4.0, 3.5, 2.75]
    write_case(case, matrix, rhs, pre, restart=2, maxiter=8, tol=1e-11, true_tol=1e-10)
    report_path = tmp_path / "report.json"
    proc = run_case(case, report_path)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(report_path.read_text())
    dense = [
        [5.0, -1.0, 0.0, 0.0],
        [-1.0, 4.0, 0.75, 0.0],
        [0.0, 0.75, 3.5, -0.5],
        [0.0, 0.0, -0.5, 2.75],
    ]
    expected = ref_gmres(dense, rhs, pre, 2, 8, 1e-11, 1e-10)
    assert len(expected[1]) > 2
    assert_report_close(report, expected)


def test_preconditioned_and_true_residuals_control_status(tmp_path):
    """A small preconditioned residual alone cannot mark a case converged."""
    case = tmp_path / "true_residual"
    matrix = """%%MatrixMarket matrix coordinate real general
2 2 2
1 1 1.0
2 2 100.0
"""
    rhs = [1.0, 1.0]
    pre = [1.0e9, 1.0e9]
    write_case(case, matrix, rhs, pre, restart=1, maxiter=1, tol=1e-6, true_tol=1e-8)
    report_path = tmp_path / "residual_report.json"
    proc = run_case(case, report_path)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(report_path.read_text())
    assert report["status"] == "not_converged"
    assert report["final_preconditioned_residual"] < 1e-6
    assert report["final_true_residual"] > 1e-8
    assert not math.isclose(
        report["final_true_residual"],
        report["final_preconditioned_residual"],
        rel_tol=1e-3,
        abs_tol=1e-12,
    )


def test_duplicate_entries_and_report_replacement_are_deterministic(tmp_path):
    """Duplicate matrix entries sum and stale reports are replaced."""
    case = tmp_path / "duplicates"
    matrix = """%%MatrixMarket matrix coordinate real general
3 3 5
1 1 2.0
1 1 1.0
2 2 4.0
3 2 -1.0
3 3 3.0
"""
    rhs = [3.0, 8.0, 3.0]
    write_case(case, matrix, rhs, None, restart=2, maxiter=6, tol=1e-12, true_tol=1e-12)
    report_path = tmp_path / "repeat.json"
    report_path.write_text('{"stale": true}')
    first = run_case(case, report_path)
    assert first.returncode == 0, first.stderr
    first_text = report_path.read_text()
    second = run_case(case, report_path)
    assert second.returncode == 0, second.stderr
    assert json.loads(report_path.read_text()) == json.loads(first_text)
    report = json.loads(first_text)
    expected = ref_gmres(
        [[3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, -1.0, 3.0]],
        rhs,
        [1.0, 1.0, 1.0],
        2,
        6,
        1e-12,
        1e-12,
    )
    assert_report_close(report, expected)


def test_invalid_case_removes_stale_report(tmp_path):
    """Invalid vector/preconditioner dimensions fail nonzero and leave no report."""
    case = tmp_path / "invalid"
    matrix = """%%MatrixMarket matrix coordinate real general
3 3 3
1 1 1.0
2 2 1.0
3 3 1.0
"""
    write_case(case, matrix, [1.0, 2.0], [1.0, 1.0, 1.0], restart=2, maxiter=3)
    assert_invalid_case(case, tmp_path / "invalid.json")


def test_invalid_preconditioner_values_are_rejected(tmp_path):
    """Nonfinite or zero preconditioner entries are invalid and leave no report."""
    matrix = """%%MatrixMarket matrix coordinate real general
2 2 2
1 1 1.0
2 2 1.0
"""
    for name, precond in [
        ("zero", [1.0, 0.0]),
        ("nonfinite", [1.0, "Inf"]),
        ("nan", [1.0, "NaN"]),
    ]:
        case = tmp_path / name
        write_case(case, matrix, [1.0, 2.0], precond, restart=2, maxiter=3)
        assert_invalid_case(case, tmp_path / f"{name}.json")


def test_invalid_solver_controls_are_rejected(tmp_path):
    """Restart, maxiter, and tolerance bounds are enforced before reporting."""
    matrix = """%%MatrixMarket matrix coordinate real general
2 2 2
1 1 1.0
2 2 1.0
"""
    bad_controls = [
        ("restart", 0, 4, 1e-8, 1e-8),
        ("maxiter", 1, -1, 1e-8, 1e-8),
        ("tol", 1, 4, 0.0, 1e-8),
        ("true_tol", 1, 4, 1e-8, float("inf")),
    ]
    for name, restart, maxiter, tol, true_tol in bad_controls:
        case = tmp_path / name
        write_case(
            case,
            matrix,
            [1.0, 2.0],
            None,
            restart=restart,
            maxiter=maxiter,
            tol=tol,
            true_tol=true_tol,
        )
        assert_invalid_case(case, tmp_path / f"{name}.json")


def test_missing_required_case_keys_are_rejected(tmp_path):
    """A case missing a required setting is invalid and leaves no report."""
    case = tmp_path / "missing_key"
    case.mkdir()
    (case / "A.mtx").write_text("""%%MatrixMarket matrix coordinate real general
2 2 2
1 1 1.0
2 2 1.0
""")
    (case / "b.txt").write_text("1.0\n2.0\n")
    (case / "case.txt").write_text(
        "matrix = A.mtx\nrhs = b.txt\nrestart = 2\nmaxiter = 4\ntol = 1e-8\n"
    )
    assert_invalid_case(case, tmp_path / "missing_key.json")


def test_invalid_matrix_dimensions_are_rejected(tmp_path):
    """Nonsquare or nonpositive Matrix Market dimensions fail without a report."""
    cases = {
        "nonsquare": """%%MatrixMarket matrix coordinate real general
2 3 2
1 1 1.0
2 2 1.0
""",
        "nonpositive": """%%MatrixMarket matrix coordinate real general
0 0 0
""",
    }
    for name, matrix in cases.items():
        case = tmp_path / name
        write_case(case, matrix, [], None, restart=1, maxiter=1)
        assert_invalid_case(case, tmp_path / f"{name}.json")


def test_malformed_matrix_market_is_rejected(tmp_path):
    """Malformed Matrix Market headers and entries fail without a report."""
    cases = {
        "bad_header": """%%MatrixMarket matrix array real general
2 2 2
1 1 1.0
2 2 1.0
""",
        "bad_entry": """%%MatrixMarket matrix coordinate real general
2 2 1
1 1
""",
        "bad_index": """%%MatrixMarket matrix coordinate real general
2 2 1
3 1 1.0
""",
    }
    for name, matrix in cases.items():
        case = tmp_path / name
        write_case(case, matrix, [1.0, 2.0], None, restart=1, maxiter=1)
        assert_invalid_case(case, tmp_path / f"{name}.json")


def test_visible_case_contract_path_still_runs(tmp_path):
    """The shipped visible case follows the same public command contract."""
    src = APP / "cases" / "visible"
    case = tmp_path / "visible"
    shutil.copytree(src, case)
    report_path = tmp_path / "visible.json"
    proc = run_case(case, report_path)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(report_path.read_text())
    assert report["restart"] == 2
    assert report["iterations"] == len(report["records"])
    assert report["status"] in {"converged", "not_converged"}
