"""Behavioral tests for the riskcalc interval engine."""

from __future__ import annotations

import json
import random
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


APP = Path("/app/task_file")
BIN = APP / "target" / "debug" / "riskcalc"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:-]{0,39}$")


@dataclass(frozen=True)
class Interval:
    """Closed interval in fixed-point ticks."""

    lo: int
    hi: int


def build_binary() -> None:
    result = subprocess.run(
        ["cargo", "build", "--quiet", "--locked"],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def run_case(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BIN), "evaluate", str(path)],
        cwd=APP,
        text=True,
        capture_output=True,
        check=False,
    )


def dec_fraction(text: str) -> Fraction:
    sign = -1 if text.startswith("-") else 1
    if text[:1] in "+-":
        text = text[1:]
    whole, dot, frac = text.partition(".")
    if not whole:
        whole = "0"
    if not whole.isdigit() or (dot and not frac.isdigit()):
        raise ValueError(text)
    den = 10 ** len(frac)
    return sign * Fraction(int(whole) * den + (int(frac) if frac else 0), den)


def floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def ceil_fraction(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def scalar_interval(text: str, scale: int) -> Interval:
    q = 10**scale
    value = dec_fraction(text) * q
    return Interval(floor_fraction(value), ceil_fraction(value))


def parse_interval(text: str, scale: int) -> Interval:
    assert text.startswith("[") and text.endswith("]")
    left, right = text[1:-1].split(",", 1)
    q = 10**scale
    lo_value = dec_fraction(left.strip()) * q
    hi_value = dec_fraction(right.strip()) * q
    interval = Interval(floor_fraction(lo_value), ceil_fraction(hi_value))
    if interval.lo > interval.hi:
        raise ValueError(text)
    return interval


def add(a: Interval, b: Interval) -> Interval:
    return Interval(a.lo + b.lo, a.hi + b.hi)


def mul(a: Interval, b: Interval, scale: int) -> Interval:
    q = 10**scale
    products = [a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi]
    return Interval(min(products) // q, ceil_fraction(Fraction(max(products), q)))


def double(a: Interval) -> Interval:
    return Interval(a.lo * 2, a.hi * 2)


def fmt_ticks(ticks: int, scale: int) -> str:
    if ticks == 0:
        sign = ""
    else:
        sign = "-" if ticks < 0 else ""
    ticks = abs(ticks)
    q = 10**scale
    whole, frac = divmod(ticks, q)
    if scale == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:0{scale}d}"


def interval_json(interval: Interval, scale: int) -> dict[str, str]:
    return {"lo": fmt_ticks(interval.lo, scale), "hi": fmt_ticks(interval.hi, scale)}


def expected_from_text(text: str) -> dict[str, object]:
    portfolio = None
    scale = None
    factors: dict[str, dict[str, object]] = {}
    positions: list[tuple[str, Interval, Interval]] = []
    position_ids: set[str] = set()
    covariances: dict[tuple[str, str], Interval] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "portfolio":
            portfolio = parts[1]
        elif parts[0] == "scale":
            scale = int(parts[1])
        elif parts[0] == "factor":
            assert scale is not None
            factors[parts[1]] = {"bucket": parts[2], "variance": parse_interval(parts[3], scale)}
        elif parts[0] == "position":
            assert scale is not None and parts[2] in factors
            assert parts[1] not in position_ids
            position_ids.add(parts[1])
            positions.append((parts[2], scalar_interval(parts[3], scale), parse_interval(parts[4], scale)))
        elif parts[0] == "cov":
            assert scale is not None
            key = tuple(sorted((parts[1], parts[2])))
            covariances[key] = parse_interval(parts[3], scale)
        else:
            raise ValueError(line)
    assert portfolio is not None and scale is not None
    assert NAME_RE.match(portfolio)

    exposures = {name: Interval(0, 0) for name in factors}
    for factor, amount, sens in positions:
        exposures[factor] = add(exposures[factor], mul(amount, sens, scale))

    buckets: dict[str, list[str]] = {}
    for factor in sorted(factors):
        buckets.setdefault(str(factors[factor]["bucket"]), []).append(factor)

    bucket_rows = []
    total_exposure = Interval(0, 0)
    total_variance = Interval(0, 0)
    for bucket in sorted(buckets):
        names = buckets[bucket]
        exposure = Interval(0, 0)
        for name in names:
            exposure = add(exposure, exposures[name])
        variance = Interval(0, 0)
        for i, left in enumerate(names):
            for right in names[i:]:
                coeff = (
                    factors[left]["variance"]
                    if left == right
                    else covariances.get(tuple(sorted((left, right))), Interval(0, 0))
                )
                term = mul(mul(exposures[left], exposures[right], scale), coeff, scale)
                if left != right:
                    term = double(term)
                variance = add(variance, term)
        total_exposure = add(total_exposure, exposure)
        total_variance = add(total_variance, variance)
        bucket_rows.append(
            {
                "bucket": bucket,
                "exposure": interval_json(exposure, scale),
                "variance": interval_json(variance, scale),
            }
        )

    return {
        "portfolio": portfolio,
        "scale": scale,
        "buckets": bucket_rows,
        "total": {
            "exposure": interval_json(total_exposure, scale),
            "variance": interval_json(total_variance, scale),
        },
    }


def write_case(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.risk"
    path.write_text(body, encoding="utf-8")
    return path


def generated_portfolio(seed: int) -> str:
    rng = random.Random(seed)
    scale = rng.choice([2, 3, 4, 5])
    buckets = ["Rates", "Credit", "Alpha"]
    factor_rows = [
        ("Zeta", buckets[0], "[-0.01025,0.01476]"),
        ("Alpha", buckets[2], "[0.019901,0.030204]"),
        ("Beta", buckets[1], "[0.005555,0.021111]"),
        ("Gamma", buckets[0], "[0.002001,0.018009]"),
        ("Delta", buckets[1], "[-0.00375,0.009125]"),
    ]
    rng.shuffle(factor_rows)
    amounts = ["123.4567", "-78.9012", "0.0049", "51.33333", "-250.019"]
    sensitivities = ["[-1.25009,-0.74991]", "[0.33331,0.77779]", "[-0.0041,0.0062]", "[1.99991,2.00009]"]
    lines = [f"portfolio Case{seed}", f"scale {scale}"]
    for row in factor_rows:
        lines.append(f"factor {row[0]} {row[1]} {row[2]}")
    for idx in range(13):
        factor = rng.choice([row[0] for row in factor_rows])
        amount = rng.choice(amounts)
        sensitivity = rng.choice(sensitivities)
        lines.append(f"position P{idx:02d} {factor} {amount} {sensitivity}")
    covs = [
        ("Gamma", "Zeta", "[-0.00501,0.00702]"),
        ("Beta", "Delta", "[0.00123,0.00678]"),
        ("Delta", "Beta", "[-0.00225,0.00445]"),
        ("Zeta", "Gamma", "[0.00005,0.00888]"),
    ]
    rng.shuffle(covs)
    for left, right, interval in covs:
        lines.append(f"cov {left} {right} {interval}")
    return "\n".join(lines) + "\n"


def noisy_replay_portfolio(seed: int) -> str:
    rng = random.Random(seed)
    factors = [
        ("Left", "A:0", "[-0.200004,0.300006]"),
        ("Right", "A:0", "[0.400004,0.500006]"),
        ("Dash", "A-0", "[-0.000014,0.000026]"),
        ("Under", "A_0", "[1.111114,1.222226]"),
        ("Lower", "a-0", "[-0.777776,-0.111114]"),
        ("Tail", "Z:tail", "[0.000004,0.000006]"),
    ]
    rng.shuffle(factors)
    amounts = ["+0.000009", "-.000011", "1234.567891", "-987.654329", "0.333335"]
    sensitivities = [
        "[-999.999991,1000.000009]",
        "[-0.000019,0.000021]",
        "[1.499991,1.500009]",
        "[-2.000009,-0.499991]",
    ]
    lines = [
        "# leading comment and blank lines are ignored",
        "",
        f"portfolio Replay{seed} # header comment",
        "scale 5",
    ]
    for factor, bucket, variance in factors:
        lines.append(f"factor {factor} {bucket} {variance} # factor row")
    factor_names = [row[0] for row in factors]
    for idx in range(24):
        lines.append(
            f"position R{idx:02d} {rng.choice(factor_names)} "
            f"{rng.choice(amounts)} {rng.choice(sensitivities)}"
        )
        if idx % 5 == 2:
            lines.append("   # interleaved comment-only row")
    lines.extend(
        [
            "cov Right Left [0.100004,0.200006]",
            "cov Left Right [-0.300006,-0.100004] # replaces previous unordered pair",
            "cov Dash Under [0.000004,0.000006]",
            "cov Under Dash [0.900004,0.900006]",
            "cov Tail Left [999.99999,999.99999] # valid but cross-bucket for variance",
            "cov Lower Dash [-999.99999,-999.99999]",
        ]
    )
    return "\n".join(lines) + "\n"


def test_generated_portfolios_match_contract(tmp_path: Path) -> None:
    """Generated valid portfolios should match the documented JSON contract."""
    build_binary()
    for seed in [4101, 4102, 4103, 4104]:
        body = generated_portfolio(seed)
        path = write_case(tmp_path, f"case_{seed}", body)
        result = run_case(path)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected_from_text(body)


def test_noisy_replay_portfolios_preserve_full_contract(tmp_path: Path) -> None:
    """Generated portfolios combine comments, byte ordering, and covariance replay."""
    build_binary()
    for seed in [8301, 8302, 8303, 8304, 8305]:
        body = noisy_replay_portfolio(seed)
        path = write_case(tmp_path, f"noisy_replay_{seed}", body)
        result = run_case(path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data == expected_from_text(body)
        assert [row["bucket"] for row in data["buckets"]] == [
            "A-0",
            "A:0",
            "A_0",
            "Z:tail",
            "a-0",
        ]


def test_large_values_are_processed_without_wrapping(tmp_path: Path) -> None:
    """Large fixed-point totals should be represented exactly or fail, never wrap."""
    build_binary()
    body = """
portfolio BigBook
scale 2
factor Fbig Risk [9.9901,10.0009]
factor Fhedge Risk [-0.2501,0.7501]
position A Fbig 123456789012.349 [2.499,2.501]
position B Fhedge -99887766554.433 [-1.125,1.875]
cov Fhedge Fbig [-0.031,0.044]
""".strip() + "\n"
    path = write_case(tmp_path, "large", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected_from_text(body)


def test_zero_exposure_never_formats_negative_zero(tmp_path: Path) -> None:
    """Zero-valued intervals should print canonical positive zero strings."""
    build_binary()
    body = """
portfolio ZeroBook
scale 4
factor Fzero Risk [0.0001,0.0002]
factor Fhedge Alpha [-0.3333,0.4444]
position P0 Fzero 0.0 [-1.0,1.0]
position P1 Fhedge 0.0 [-0.5,0.5]
""".strip() + "\n"
    path = write_case(tmp_path, "zero", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    rendered = json.dumps(data, sort_keys=True)
    assert "-0" not in rendered
    assert data["total"]["exposure"] == {"lo": "0.0000", "hi": "0.0000"}
    assert data["total"]["variance"] == {"lo": "0.0000", "hi": "0.0000"}


def test_cross_endpoint_products_and_covariance_replacement(tmp_path: Path) -> None:
    """Mixed-sign intervals need all endpoint products and last covariance wins."""
    build_binary()
    body = """
portfolio CrossBook
scale 2
factor Fmix B:2 [-5.00,7.00]
factor Fother B:2 [1.00,1.00]
factor Falpha A-1 [0.10,0.20]
position M0 Fmix 1.00 [-2.00,3.00]
position O0 Fother -4.00 [0.25,0.50]
position A0 Falpha 0.333 [0.333,0.667]
cov Fmix Fother [9.00,10.00]
cov Fother Fmix [-0.50,0.25]
""".strip() + "\n"
    path = write_case(tmp_path, "cross", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == expected_from_text(body)
    assert [row["bucket"] for row in data["buckets"]] == ["A-1", "B:2"]


def test_scale_zero_and_micro_scale_interactions(tmp_path: Path) -> None:
    """Boundary scales still need outward rounding and quadratic aggregation."""
    build_binary()
    cases = {
        "scale_zero": """
portfolio Round0
scale 0
factor Fa Zeta [0.49,1.01]
factor Fb Alpha [-2.01,-0.49]
factor Fc Alpha [1.01,1.99]
position A Fa -3.51 [-1.49,0.51]
position B Fb 2.49 [-2.49,-1.51]
position C Fc -0.49 [-3.01,3.01]
cov Fb Fc [-0.51,0.51]
cov Fc Fb [1.49,2.01]
""".strip()
        + "\n",
        "scale_six": """
portfolio Micro6
scale 6
factor Fleft A:micro [-0.0000014,0.0000024]
factor Fright A:micro [-0.0000034,0.0000044]
factor Fsolo Z-last [0.1111111,0.2222222]
position L0 Fleft 123.4567894 [-0.0000011,0.0000021]
position R0 Fright -987.6543216 [-0.0000031,0.0000041]
position S0 Fsolo 0.0000004 [-3.3333333,4.4444444]
cov Fleft Fright [-0.9999999,1.0000001]
cov Fright Fleft [-0.1250001,0.2500001]
""".strip()
        + "\n",
    }
    for name, body in cases.items():
        path = write_case(tmp_path, name, body)
        result = run_case(path)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected_from_text(body)


def test_scale_zero_negative_signs_survive_covariance_replay(tmp_path: Path) -> None:
    """Whole-unit formatting must preserve negative signs while replaying sparse terms."""
    build_binary()
    cases = {
        "sign_zero_a": """
portfolio WholeNegA
scale 0
factor Left B:10 [0.49,1.51]
factor Right B:10 [-1.51,0.49]
factor Solo A-00 [0.00,0.00]
position L0 Left -5.49 [0.49,1.51]
position L1 Left +0.49 [-2.51,-1.49]
position R0 Right 3.49 [-2.51,-1.49]
position S0 Solo -0.49 [0.00,0.00]
cov Left Right [-0.51,0.51] # replaced by the later unordered duplicate
cov Right Left [1.49,2.51]
""".strip()
        + "\n",
        "sign_zero_b": """
portfolio WholeNegB
scale 0
factor Fx aa:tail [1.01,1.99]
factor Fy aa:tail [-0.99,0.99]
factor Fz AA:head [0.00,0.00]
position X0 Fx -.51 [3.49,4.51]
position X1 Fx -7.49 [0.49,1.51]
position Y0 Fy 2.49 [-3.51,-2.49]
position Z0 Fz +0.49 [-0.49,0.49]
cov Fy Fx [-3.51,-2.49]
cov Fx Fy [-0.51,0.51]
""".strip()
        + "\n",
    }
    for name, body in cases.items():
        path = write_case(tmp_path, name, body)
        result = run_case(path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        expected = expected_from_text(body)
        assert data == expected
        rendered = json.dumps(data, sort_keys=True)
        assert re.search(r'"lo":\s*"-[1-9][0-9]*"', rendered)
        assert re.search(r'"hi":\s*"-[1-9][0-9]*"', rendered)
        assert '"-0"' not in rendered


def test_invalid_inputs_are_rejected(tmp_path: Path) -> None:
    """Invalid portfolio files should produce a non-zero exit status."""
    build_binary()
    invalid_bodies = [
        "portfolio Bad\nscale 3\nposition P Missing 1.0 [0,1]\n",
        "scale 3\nportfolio Bad\nfactor F B [0,1]\n",
        "portfolio Bad\nscale 7\nfactor F B [0,1]\n",
        "portfolio Bad\nscale 2\nfactor F B [2.0,1.0]\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\nposition P F 1.0 [2.0,1.0]\n",
        "portfolio Bad\nscale 2\nfactor A B [0,1]\nfactor C B [0,1]\ncov A C [2.0,1.0]\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\ncov F F [0,1]\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\nposition Same F 1 [0,1]\nposition Same F 2 [0,1]\n",
    ]
    for idx, body in enumerate(invalid_bodies):
        path = write_case(tmp_path, f"invalid_{idx}", body)
        result = run_case(path)
        assert result.returncode != 0
        assert result.stderr.strip()


def test_strict_record_shapes_and_decimal_syntax_are_rejected(tmp_path: Path) -> None:
    """Malformed records and decimal spellings should not be accepted silently."""
    build_binary()
    invalid_bodies = [
        "portfolio Bad\nscale 2\nfactor F B [0,1] extra\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\nposition P F 1 [0,1] trailing\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\ncov F Missing [0,1]\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\nposition P F + [0,1]\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\nposition P F . [0,1]\n",
        "portfolio Bad\nscale 2\nfactor F B [0,1]\nposition P F 1e2 [0,1]\n",
        "portfolio Bad\nscale 2\nfactor F Bucketé [0,1]\n",
    ]
    for idx, body in enumerate(invalid_bodies):
        path = write_case(tmp_path, f"strict_invalid_{idx}", body)
        result = run_case(path)
        assert result.returncode != 0
        assert result.stderr.strip()


def test_output_is_repeatable(tmp_path: Path) -> None:
    """Repeated evaluations of the same portfolio should produce identical bytes."""
    build_binary()
    body = generated_portfolio(5201)
    path = write_case(tmp_path, "repeat", body)
    outputs = []
    for _ in range(3):
        result = run_case(path)
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert json.loads(outputs[0]) == expected_from_text(body)


def test_compound_rounding_ordering_and_missing_covariance(tmp_path: Path) -> None:
    """A compound valid portfolio stresses ordering, rounding, and sparse covariance."""
    build_binary()
    body = """
portfolio CompoundBackstop
scale 5
factor Fz Z-last [-0.000019,0.000021]
factor Fa A-First [0.333333,0.666667]
factor Fcolon A:later [-0.125001,0.250001]
factor Fdash A-later [-0.222229,0.111119]
position P0 Fz -12.345678 [-0.010001,0.020001]
position P1 Fa 0.000009 [-12345.678901,23456.789012]
position P2 Fcolon -0.000011 [-9999.999999,8888.888888]
position P3 Fdash 3.141592 [-0.000031,0.000041]
position P4 Fdash -2.718281 [-0.000051,0.000061]
cov Fdash Fcolon [0.123456,0.234567]
cov Fcolon Fdash [-0.345679,-0.123457]
cov Fa Fdash [-0.000009,0.000011]
""".strip() + "\n"
    path = write_case(tmp_path, "compound_backstop", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == expected_from_text(body)
    assert [row["bucket"] for row in data["buckets"]] == ["A-First", "A-later", "A:later", "Z-last"]


def test_generated_sparse_replacement_matrix(tmp_path: Path) -> None:
    """Generated mixed-bucket portfolios combine replacement and sign edges."""
    build_binary()
    factor_rows = [
        ("Zed", "m:2", "[-0.0000014,0.0000026]"),
        ("Kappa", "A:0", "[-0.3333334,0.6666667]"),
        ("Beta", "A-9", "[0.1111114,0.2222226]"),
        ("Alfa", "A:0", "[-0.1250006,0.2500004]"),
        ("Mu", "m:2", "[0.0000034,0.0000046]"),
        ("Eta", "A-9", "[-0.7777776,-0.1111114]"),
    ]
    amount_pool = ["-0.0000004", "0.0000005", "999.9999995", "-123.4567894", "3.3333333"]
    sensitivity_pool = [
        "[-9.0000004,8.0000005]",
        "[-0.0000014,0.0000024]",
        "[1.2345674,1.2345676]",
        "[-2.5000005,-0.5000004]",
    ]
    for seed in [7201, 7202, 7203]:
        rng = random.Random(seed)
        rows = factor_rows[:]
        rng.shuffle(rows)
        lines = [f"portfolio SparseMatrix{seed}", "scale 6"]
        for factor, bucket, variance in rows:
            lines.append(f"factor {factor} {bucket} {variance}")
        names = [row[0] for row in rows]
        for idx in range(18):
            lines.append(
                f"position X{idx:02d} {rng.choice(names)} "
                f"{rng.choice(amount_pool)} {rng.choice(sensitivity_pool)}"
            )
        lines.extend(
            [
                "cov Kappa Alfa [0.5000004,0.7500006]",
                "cov Alfa Kappa [-0.2500006,0.1250004]",
                "cov Mu Zed [-0.0000016,0.0000026]",
                "cov Zed Mu [0.3333334,0.3333336]",
                "cov Eta Beta [-0.4444446,-0.1111114]",
            ]
        )
        body = "\n".join(lines) + "\n"
        path = write_case(tmp_path, f"sparse_matrix_{seed}", body)
        result = run_case(path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data == expected_from_text(body)
        assert [row["bucket"] for row in data["buckets"]] == ["A-9", "A:0", "m:2"]


def test_layered_byte_order_rounding_and_sparse_covariance_holdout(tmp_path: Path) -> None:
    """One valid portfolio combines bytewise buckets, scalar containment, signs, and covariance replacement."""
    build_binary()
    body = """
portfolio LayeredEdge
scale 3
factor FactorZ z:tail [-0.0049,0.0061]
factor FactorA A-0 [1.0004,1.0006]
factor FactorB A-0 [-0.2506,0.5004]
factor FactorC A:0 [-2.0004,-1.9996]
factor FactorD A:0 [0.3334,0.6666]
factor FactorE a-0 [-0.0014,0.0016]
position P0 FactorA -1.2345 [-2.3456,3.4567]
position P1 FactorB 0.0004 [-999.9996,1000.0004]
position P2 FactorC 12.3456 [-0.0044,0.0056]
position P3 FactorD -9876.5434 [0.0004,0.0006]
position P4 FactorE 0.0004 [-0.0004,0.0006]
position P5 FactorZ -250.5555 [-1.0004,-0.9996]
position P6 FactorA 2.0005 [-0.5005,-0.4995]
cov FactorB FactorA [0.1004,0.1006]
cov FactorA FactorB [-0.2006,-0.1004]
cov FactorC FactorD [0.0004,0.0016]
cov FactorD FactorC [-0.0024,0.0036]
cov FactorZ FactorA [0.9994,1.0006]
""".strip() + "\n"
    path = write_case(tmp_path, "layered_edge", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == expected_from_text(body)
    assert [row["bucket"] for row in data["buckets"]] == ["A-0", "A:0", "a-0", "z:tail"]


def test_arithmetic_overflow_is_rejected_without_wrapping(tmp_path: Path) -> None:
    """Valid syntax that exceeds i128 arithmetic range must fail, not wrap."""
    build_binary()
    body = """
portfolio OverflowBackstop
scale 0
factor Fbig Risk [2,2]
position P0 Fbig 13000000000000000000 [13000000000000000000,13000000000000000000]
""".strip() + "\n"
    path = write_case(tmp_path, "overflow_backstop", body)
    result = run_case(path)
    assert result.returncode != 0
    assert result.stderr.strip()


def test_inline_comments_duplicate_ids_and_bytewise_bucket_edges(tmp_path: Path) -> None:
    """Inline comments, unique ids, and bytewise buckets interact with replacement covariances."""
    build_binary()
    body = """
portfolio CommentedEdges # trailing portfolio comment is ignored
scale 4 # output precision
factor Zulu z:tail [-0.00019,0.00021] # later in bytewise order
factor Alpha A-0 [0.333333,0.666667]
factor Beta A:0 [-0.125001,0.250001]
factor Gamma A:0 [-0.222229,0.111119]
position P0 Zulu -12.345678 [-0.010001,0.020001] # scalar containment
position P1 Alpha +0.000009 [-12345.678901,23456.789012]
position P2 Beta -.000011 [-9999.999999,8888.888888]
position P3 Gamma 3.141592 [-0.000031,0.000041]
position P4 Gamma -2.718281 [-0.000051,0.000061]
cov Gamma Beta [0.123456,0.234567]
cov Beta Gamma [-0.345679,-0.123457] # replaces unordered pair
cov Alpha Gamma [-0.000009,0.000011]
""".strip() + "\n"
    path = write_case(tmp_path, "commented_edges", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == expected_from_text(body)
    assert [row["bucket"] for row in data["buckets"]] == ["A-0", "A:0", "z:tail"]


def test_long_fractional_precision_does_not_trigger_shortcut_caps(tmp_path: Path) -> None:
    """Values with many valid fractional digits are rounded, not rejected or truncated early."""
    build_binary()
    body = """
portfolio FractionalDepth
scale 6
factor NanoA Risk [-0.000000000004,0.000001999996]
factor NanoB Risk [-0.000002500004,0.000003500006]
factor Hedge Other [0.111111111111,0.222222222222]
position A0 NanoA 123456.789012345678 [-0.000000400001,0.000000600001]
position A1 NanoA -0.000000400001 [1.499999400001,1.500000600001]
position B0 NanoB -98765.432109876543 [-0.000001600001,-0.000000400001]
position H0 Hedge 0.000000499999 [-3.333333333333,4.444444444444]
cov NanoB NanoA [-0.000000400001,0.000000700001]
""".strip() + "\n"
    path = write_case(tmp_path, "fractional_depth", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected_from_text(body)


def test_extreme_fractional_depth_interacts_with_replay_and_ordering(tmp_path: Path) -> None:
    """Very long decimal tails still obey outward rounding, sparse replay, and byte ordering."""
    build_binary()
    tiny_tail = "000000000000004"
    body = f"""
portfolio DeepFractional # inline comments still do not cap decimal parsing
scale 6
factor LongB B:2 [-0.{tiny_tail},0.000001{tiny_tail}]
factor LongA A-long [0.333333333333333333,0.333333333333333337]
factor LongC B:2 [-0.250000000000000001,0.250000000000000001]
position P0 LongB 1.000000000000000004 [1.000000000000000004,1.000000000000000006]
position P1 LongC -.{tiny_tail} [-2.000000000000000006,-1.999999999999999994]
position P2 LongA 2.500000000000000004 [-0.400000000000000006,0.600000000000000004]
cov LongC LongB [0.010000000000000001,0.020000000000000001]
cov LongB LongC [-0.030000000000000001,0.040000000000000001] # replacement
""".strip() + "\n"
    path = write_case(tmp_path, "extreme_fractional_depth", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == expected_from_text(body)
    assert [row["bucket"] for row in data["buckets"]] == ["A-long", "B:2"]


def test_cross_bucket_covariances_do_not_leak_into_bucket_variance(tmp_path: Path) -> None:
    """Sparse covariance is only used for factor pairs that share the same bucket."""
    build_binary()
    body = """
portfolio CrossBucketHoldout
scale 3
factor SameA Shared [1.001,1.009]
factor SameB Shared [-0.501,0.501]
factor OtherA Other [-9.999,10.001]
factor Tail zzz [0.004,0.006]
position S0 SameA -1.2345 [-2.3456,3.4567]
position S1 SameB 0.0004 [-999.9996,1000.0004]
position O0 OtherA 12.3456 [-0.0044,0.0056]
position T0 Tail -250.5555 [-1.0004,-0.9996]
cov SameA SameB [-0.2006,-0.1004]
cov SameA OtherA [999.999,999.999]
cov Tail SameA [-999.999,-999.999]
""".strip() + "\n"
    path = write_case(tmp_path, "cross_bucket_holdout", body)
    result = run_case(path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == expected_from_text(body)
    assert [row["bucket"] for row in data["buckets"]] == ["Other", "Shared", "zzz"]
