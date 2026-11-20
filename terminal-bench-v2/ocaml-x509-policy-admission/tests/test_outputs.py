import subprocess
from itertools import permutations
from pathlib import Path

APP = Path("/app/certadmit")


def run_case(tmp_path, trust_lines, bundle_lines, crl_groups, host, now=1_700_000_000):
    tmp_path.mkdir(parents=True, exist_ok=True)
    trust = tmp_path / "trust.txt"
    bundle = tmp_path / "bundle.txt"
    crldir = tmp_path / "crl"
    crldir.mkdir()
    trust.write_text("\n".join(trust_lines) + "\n")
    bundle.write_text("\n".join(bundle_lines) + "\n")
    for idx, lines in enumerate(crl_groups):
        (crldir / f"{idx}.crl").write_text("\n".join(lines) + "\n")
    return subprocess.run(
        [str(APP), "--trust", str(trust), "--crl", str(crldir), "--now", str(now), "--host", host, "--bundle", str(bundle)],
        text=True,
        capture_output=True,
        check=False,
    )


def setup_module(module):
    """The admission command can be rebuilt from the shipped source."""
    subprocess.run(["make", "-C", "/app", "clean", "all"], check=True)


def cert(cid, subj, issuer, serial, ca, nb=1_600_000_000, na=1_800_000_000,
          ku="-", eku="-", dns="-", ip="-", permit="-", exclude="-", aki="-",
          path_len="-", policies="serverTLS", policy_maps="-"):
    return "|".join([
        "cert", cid, subj, issuer, serial, "true" if ca else "false", str(nb), str(na),
        ku, eku, dns, ip, permit, exclude, aki, str(path_len), policies, policy_maps,
    ])


def crl(issuer, this_update=1_690_000_000, next_update=1_710_000_000, serials="-",
        number=1, base="-"):
    return "|".join([
        "crl", issuer, str(number), str(base), str(this_update), str(next_update), serials,
    ])


def test_case_a(tmp_path):
    """A simple admissible chain is accepted."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "100", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "300", False, ku="digitalSignature", eku="serverAuth", dns="api.example.test", aki="M"),
            cert("M", "Mid", "Root", "200", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_b(tmp_path):
    """An admissible chain is found when several candidate issuers share a subject."""
    res = run_case(
        tmp_path,
        [
            cert("R1", "Root", "Root", "101", True, ku="keyCertSign", permit="corp.test"),
            cert("R2", "Root", "Root", "102", True, ku="keyCertSign"),
        ],
        [
            cert("L", "leaf", "Mid", "301", False, ku="digitalSignature", eku="serverAuth", dns="service.example.org", aki="M"),
            cert("M", "Mid", "Root", "201", True, ku="keyCertSign", aki="R2"),
        ],
        [[crl("Mid"), crl("Root")]],
        "service.example.org",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_c(tmp_path):
    """Inherited issuer policy is enforced on the admitted identity."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "110", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "310", False, ku="digitalSignature", eku="serverAuth", dns="api.outside.test", aki="M"),
            cert("M", "Mid", "Root", "210", True, ku="keyCertSign", permit="inside.test", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.outside.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_d(tmp_path):
    """Fresh issuer status evidence is required for admission."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "120", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "320", False, ku="digitalSignature", eku="serverAuth", dns="fresh.example.test", aki="M"),
            cert("M", "Mid", "Root", "220", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid", next_update=1_699_999_999), crl("Root")]],
        "fresh.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CRL_STALE", "")


def test_case_e(tmp_path):
    """Issuer status evidence covers each certificate below the trust anchor."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "130", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "330", False, ku="digitalSignature", eku="serverAuth", dns="status.example.test", aki="M"),
            cert("M", "Mid", "Root", "230", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root", serials="230")]],
        "status.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:REVOKED", "")


def test_case_f(tmp_path):
    """DNS names are admitted only when a SAN pattern covers the requested host."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "140", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "340", False, ku="digitalSignature", eku="serverAuth", dns="*.example.test", aki="M"),
            cert("M", "Mid", "Root", "240", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "deep.api.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:IDENTITY", "")


def test_case_g(tmp_path):
    """IP addresses are admitted only by exact SAN equality."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "150", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "350", False, ku="digitalSignature", eku="serverAuth", ip="0.0.5", aki="M"),
            cert("M", "Mid", "Root", "250", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "10.0.0.5",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:IDENTITY", "")


def test_case_h(tmp_path):
    """Malformed input records are rejected before admission decisions."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "160", True, ku="keyCertSign")],
        ["cert|L|leaf|Root"],
        [[crl("Root")]],
        "bad.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")


def test_case_i(tmp_path):
    """A bundle without a chain to any trust anchor yields NO_PATH."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "170", True, ku="keyCertSign")],
        [cert("L", "leaf", "Missing", "370", False, ku="digitalSignature", eku="serverAuth", dns="lost.example.test", aki="Z")],
        [[crl("Missing")]],
        "lost.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NO_PATH", "")


def test_case_j(tmp_path):
    """A time-invalid certificate on an otherwise linked path is rejected."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "180", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "380", False, ku="digitalSignature", eku="serverAuth", dns="time.example.test", aki="M"),
            cert("M", "Mid", "Root", "280", True, nb=1_500_000_000, na=1_650_000_000, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "time.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:TIME", "")


def test_case_k(tmp_path):
    """An issuer certificate without signing authority is rejected."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "190", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "390", False, ku="digitalSignature", eku="serverAuth", dns="policy.example.test", aki="M"),
            cert("M", "Mid", "Root", "290", True, ku="digitalSignature", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "policy.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CA_POLICY", "")


def test_case_l(tmp_path):
    """A leaf missing the required application policy is rejected."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "191", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "391", False, ku="digitalSignature", eku="clientAuth", dns="leaf.example.test", aki="M"),
            cert("M", "Mid", "Root", "291", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "leaf.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_m(tmp_path):
    """A certificate marked as a CA cannot be admitted as the leaf."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "192", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "392", True, ku="keyCertSign,digitalSignature", eku="serverAuth", dns="ca-leaf.example.test", aki="R")],
        [[crl("Root")]],
        "ca-leaf.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_n(tmp_path):
    """Excluded inherited name constraints reject covered DNS identities."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "193", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "393", False, ku="digitalSignature", eku="serverAuth", dns="api.blocked.test", aki="M"),
            cert("M", "Mid", "Root", "293", True, ku="keyCertSign", exclude="blocked.test", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.blocked.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_o(tmp_path):
    """CRL freshness windows include both endpoints."""
    now = 1_700_000_000
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "194", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "394", False, ku="digitalSignature", eku="serverAuth", dns="edge.example.test", aki="M"),
            cert("M", "Mid", "Root", "294", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid", this_update=now, next_update=now), crl("Root", this_update=now, next_update=now)]],
        "edge.example.test",
        now=now,
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_p(tmp_path):
    """Issuer selection honors a non-empty authority key identifier."""
    res = run_case(
        tmp_path,
        [
            cert("R-good", "Root", "Root", "195", True, nb=1_500_000_000, na=1_650_000_000, ku="keyCertSign"),
            cert("R-bad", "Root", "Root", "196", True, ku="keyCertSign"),
        ],
        [
            cert("L", "leaf", "Root", "395", False, ku="digitalSignature", eku="serverAuth", dns="aki.example.test", aki="R-good"),
        ],
        [[crl("Root")]],
        "aki.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:TIME", "")


def test_case_q(tmp_path):
    """CRL freshness is checked for an intermediate certificate as well as the leaf."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "196", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "396", False, ku="digitalSignature", eku="serverAuth", dns="intermediate-crl.example.test", aki="M"),
            cert("M", "Mid", "Root", "296", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root", next_update=1_699_999_999)]],
        "intermediate-crl.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CRL_STALE", "")


def test_case_r(tmp_path):
    """Inherited address constraints apply to IP SAN identities."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "197", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "397", False, ku="digitalSignature", eku="serverAuth", ip="10.0.0.7", aki="M"),
            cert("M", "Mid", "Root", "297", True, ku="keyCertSign", permit="10.0.0.5", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "10.0.0.7",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_s(tmp_path):
    """Inherited constraints are enforced below a non-adjacent issuer."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "198", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "398", False, ku="digitalSignature", eku="serverAuth", dns="api.outside.test", aki="M"),
            cert("M", "Mid", "Upper", "298", True, ku="keyCertSign", permit="inside.test", aki="U"),
            cert("U", "Upper", "Root", "299", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Upper"), crl("Root")]],
        "api.outside.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_t(tmp_path):
    """A linked CA candidate is rejected by leaf policy even when another bundle cert has no path."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "199", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Missing", "399", False, ku="digitalSignature", eku="serverAuth", dns="ca-candidate.example.test", aki="Z"),
            cert("C", "ca-leaf", "Root", "499", True, ku="keyCertSign,digitalSignature", eku="serverAuth", dns="ca-candidate.example.test", aki="R"),
        ],
        [[crl("Missing"), crl("Root")]],
        "ca-candidate.example.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_u(tmp_path):
    """DNS SAN matching accepts a host with different letter case."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "201", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "401", False, ku="digitalSignature", eku="serverAuth", dns="API.Mixed.Example", aki="M"),
            cert("M", "Mid", "Root", "501", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.mixed.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_v(tmp_path):
    """IP SAN equality can admit a valid certificate path."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "202", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "402", False, ku="digitalSignature", eku="serverAuth", ip="10.0.0.8", aki="M"),
            cert("M", "Mid", "Root", "502", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "10.0.0.8",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_w(tmp_path):
    """A one-label wildcard DNS SAN admits the covered host."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "203", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "403", False, ku="digitalSignature", eku="serverAuth", dns="*.wild.example", aki="M"),
            cert("M", "Mid", "Root", "503", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.wild.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_x(tmp_path):
    """An absent authority key identifier prevents subject-only path construction."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "204", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "404", False, ku="digitalSignature", eku="serverAuth", dns="aki-required.example", aki="-")],
        [[crl("Root")]],
        "aki-required.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NO_PATH", "")


def test_case_y(tmp_path):
    """A fresh non-revoking issuer CRL is sufficient when stale issuer CRLs are also present."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "205", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "405", False, ku="digitalSignature", eku="serverAuth", dns="multi-crl.example", aki="M"),
            cert("M", "Mid", "Root", "505", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid", next_update=1_699_999_999, serials="405", number=2), crl("Mid", number=1), crl("Root")]],
        "multi-crl.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_z(tmp_path):
    """When several validation failures apply, the documented rejection priority is used."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "206", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "406", False, nb=1_500_000_000, na=1_650_000_000, ku="digitalSignature", eku="serverAuth", dns="priority.example", aki="M"),
            cert("M", "Mid", "Root", "506", True, ku="digitalSignature", aki="R"),
        ],
        [[crl("Mid", serials="406"), crl("Root")]],
        "other.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:TIME", "")


def test_case_aa(tmp_path):
    """Path-length limits apply cumulatively through a linked hierarchy."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "600", True, ku="keyCertSign", path_len=0)],
        [
            cert("L", "leaf", "Mid", "602", False, ku="digitalSignature", eku="serverAuth", dns="depth.example", aki="M"),
            cert("M", "Mid", "Root", "601", True, ku="keyCertSign", aki="R", path_len=0),
        ],
        [[crl("Mid"), crl("Root")]],
        "depth.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CA_POLICY", "")


def test_case_ab(tmp_path):
    """A hierarchy at every CA's path-length boundary remains admissible."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "610", True, ku="keyCertSign", path_len=1)],
        [
            cert("L", "leaf", "Mid", "612", False, ku="digitalSignature", eku="serverAuth", dns="bounded.example", aki="M"),
            cert("M", "Mid", "Root", "611", True, ku="keyCertSign", aki="R", path_len=0),
        ],
        [[crl("Mid"), crl("Root")]],
        "bounded.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_ac(tmp_path):
    """Inherited constraints validate every leaf subject alternative name."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "620", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "622", False, ku="digitalSignature", eku="serverAuth", dns="api.inside.test,leak.outside.test", aki="M"),
            cert("M", "Mid", "Root", "621", True, ku="keyCertSign", permit="inside.test", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.inside.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_ad(tmp_path):
    """DNS and IP permit sets constrain only names of their own type."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "630", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "632", False, ku="digitalSignature", eku="serverAuth", dns="api.inside.test", ip="10.0.0.8", aki="M"),
            cert("M", "Mid", "Root", "631", True, ku="keyCertSign", permit="inside.test,10.0.0.8", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.inside.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_ae(tmp_path):
    """The greatest-numbered fresh full CRL determines the base revocation state."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "640", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "641", False, ku="digitalSignature", eku="serverAuth", dns="generation.example", aki="R")],
        [[crl("Root", number=2), crl("Root", number=7, serials="641")]],
        "generation.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:REVOKED", "")


def test_case_af(tmp_path):
    """A stale higher-numbered full CRL does not displace a fresh generation."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "650", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "651", False, ku="digitalSignature", eku="serverAuth", dns="fresh-generation.example", aki="R")],
        [[crl("Root", number=9, next_update=1_699_999_999, serials="651"), crl("Root", number=4)]],
        "fresh-generation.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_ag(tmp_path):
    """Only fresh higher-numbered deltas for the selected base affect revocation."""
    common = [cert("R", "Root", "Root", "660", True, ku="keyCertSign")]
    leaf = [cert("L", "leaf", "Root", "661", False, ku="digitalSignature", eku="serverAuth", dns="delta.example", aki="R")]
    revoked = run_case(
        tmp_path / "revoked", common, leaf,
        [[crl("Root", number=4), crl("Root", number=6, base=4, serials="661")]],
        "delta.example",
    )
    ignored = run_case(
        tmp_path / "ignored", common, leaf,
        [[crl("Root", number=4), crl("Root", number=8, base=3, serials="661"), crl("Root", number=3)]],
        "delta.example",
    )
    assert (revoked.returncode, revoked.stdout.strip(), revoked.stderr) == (1, "REJECT:REVOKED", "")
    assert (ignored.returncode, ignored.stdout.strip(), ignored.stderr) == (0, "ACCEPT", "")


def test_case_ah(tmp_path):
    """Admission is invariant under bundle and CRL record ordering."""
    trust = [cert("R", "Root", "Root", "670", True, ku="keyCertSign", path_len=1)]
    bundle = [
        cert("L", "leaf", "Mid", "672", False, ku="digitalSignature", eku="serverAuth", dns="ordered.example", aki="M"),
        cert("M", "Mid", "Root", "671", True, ku="keyCertSign", aki="R", path_len=0),
    ]
    status = [crl("Root", number=2), crl("Mid", number=3), crl("Mid", number=4, base=3)]
    for idx, (bundle_order, crl_order) in enumerate(zip(permutations(bundle), permutations(status))):
        res = run_case(tmp_path / str(idx), trust, list(bundle_order), [list(crl_order)], "ordered.example")
        assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_ai(tmp_path):
    """A linked issuer without CA authority contributes a CA policy rejection."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "680", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "682", False, ku="digitalSignature", eku="serverAuth", dns="issuer-policy.example", aki="M"),
            cert("M", "Mid", "Root", "681", False, ku="keyCertSign", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "issuer-policy.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CA_POLICY", "")


def test_case_aj(tmp_path):
    """A delta CRL cannot supply status without a fresh full base CRL."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "690", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "691", False, ku="digitalSignature", eku="serverAuth", dns="base-required.example", aki="R")],
        [[crl("Root", number=5, base=3)]],
        "base-required.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CRL_STALE", "")


def test_case_ak(tmp_path):
    """The end entity requires signing key usage independently of its application policy."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "700", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "701", False, ku="keyEncipherment", eku="serverAuth", dns="usage.example", aki="R")],
        [[crl("Root")]],
        "usage.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_al(tmp_path):
    """A stale delta for the selected base does not alter its full CRL."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "710", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "711", False, ku="digitalSignature", eku="serverAuth", dns="stale-delta.example", aki="R")],
        [[crl("Root", number=4), crl("Root", number=6, base=4, serials="711", next_update=1_699_999_999)]],
        "stale-delta.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_am(tmp_path):
    """One admissible linked end entity is sufficient among several candidates."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "720", True, ku="keyCertSign")],
        [
            cert("L1", "old", "Root", "721", False, nb=1_500_000_000, na=1_650_000_000, ku="digitalSignature", eku="serverAuth", dns="candidate.example", aki="R"),
            cert("L2", "current", "Root", "722", False, ku="digitalSignature", eku="serverAuth", dns="candidate.example", aki="R"),
        ],
        [[crl("Root")]],
        "candidate.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_an(tmp_path):
    """Rejection priority is stable across separate linked end entities."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "730", True, ku="keyCertSign")],
        [
            cert("L1", "old", "Root", "731", False, nb=1_500_000_000, na=1_650_000_000, ku="digitalSignature", eku="serverAuth", dns="priority-candidates.example", aki="R"),
            cert("L2", "revoked", "Root", "732", False, ku="digitalSignature", eku="serverAuth", dns="priority-candidates.example", aki="R"),
        ],
        [[crl("Root", serials="732")]],
        "priority-candidates.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:TIME", "")


def test_case_ao(tmp_path):
    """Prefixed DNS and IP permit constraints admit same-type SAN identities."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "740", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "742", False, ku="digitalSignature", eku="serverAuth", dns="api.allowed.test", ip="10.0.0.8", aki="M"),
            cert("M", "Mid", "Root", "741", True, ku="keyCertSign", permit="dns:allowed.test,ip:10.0.0.8", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "api.allowed.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_ap(tmp_path):
    """Prefixed IP exclude constraints reject the covered IP SAN exactly."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "750", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "752", False, ku="digitalSignature", eku="serverAuth", ip="10.0.0.9", aki="M"),
            cert("M", "Mid", "Root", "751", True, ku="keyCertSign", exclude="ip:10.0.0.9", aki="R"),
        ],
        [[crl("Mid"), crl("Root")]],
        "10.0.0.9",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_aq(tmp_path):
    """Trust anchor name constraints apply to every leaf SAN on the path."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "760", True, ku="keyCertSign", exclude="dns:blocked.test")],
        [
            cert("L", "leaf", "Root", "761", False, ku="digitalSignature", eku="serverAuth", dns="api.allowed.test,leak.blocked.test", aki="R"),
        ],
        [[crl("Root")]],
        "api.allowed.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NAME_CONSTRAINT", "")


def test_case_ar(tmp_path):
    """Linked non-CA leaf candidates suppress linked CA leaf candidates after evaluation."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "770", True, ku="keyCertSign")],
        [
            cert("C", "ca-leaf", "Root", "771", True, nb=1_500_000_000, na=1_650_000_000, ku="keyCertSign,digitalSignature", eku="serverAuth", dns="choice.example", aki="R"),
            cert("L", "leaf", "Root", "772", False, ku="keyEncipherment", eku="serverAuth", dns="choice.example", aki="R"),
        ],
        [[crl("Root")]],
        "choice.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_as(tmp_path):
    """A deeper hierarchy is admissible when every CA path-length boundary is met."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "780", True, ku="keyCertSign", permit="svc.test", path_len=2)],
        [
            cert("L", "leaf", "Mid", "783", False, ku="digitalSignature", eku="serverAuth", dns="api.svc.test", aki="M"),
            cert("M", "Mid", "Upper", "782", True, ku="keyCertSign", aki="U", path_len=0),
            cert("U", "Upper", "Root", "781", True, ku="keyCertSign", aki="R", path_len=1),
        ],
        [[crl("Mid")], [crl("Upper")], [crl("Root")]],
        "api.svc.test",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_at(tmp_path):
    """The trust anchor path-length bound rejects excess subordinate CAs."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "790", True, ku="keyCertSign", path_len=1)],
        [
            cert("L", "leaf", "Mid", "793", False, ku="digitalSignature", eku="serverAuth", dns="too-deep.example", aki="M"),
            cert("M", "Mid", "Upper", "792", True, ku="keyCertSign", aki="U", path_len=0),
            cert("U", "Upper", "Root", "791", True, ku="keyCertSign", aki="R", path_len=1),
        ],
        [[crl("Mid")], [crl("Upper")], [crl("Root")]],
        "too-deep.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CA_POLICY", "")


def test_case_au(tmp_path):
    """Fresh deltas for the selected full CRL can revoke a non-leaf child."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "800", True, ku="keyCertSign", path_len=2)],
        [
            cert("L", "leaf", "Mid", "803", False, ku="digitalSignature", eku="serverAuth", dns="delta-chain.example", aki="M"),
            cert("M", "Mid", "Upper", "802", True, ku="keyCertSign", aki="U", path_len=0),
            cert("U", "Upper", "Root", "801", True, ku="keyCertSign", aki="R", path_len=1),
        ],
        [
            [crl("Mid")],
            [crl("Upper")],
            [crl("Root", number=9, next_update=1_699_999_999, serials="801"), crl("Root", number=4), crl("Root", number=6, base=4, serials="801")],
        ],
        "delta-chain.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:REVOKED", "")


def test_case_av(tmp_path):
    """Invalid numeric fields make the input malformed rather than pathless."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "810", True, ku="keyCertSign")],
        ["cert|L|leaf|Root|811|false|not-a-time|1800000000|digitalSignature|serverAuth|bad.example|-|-|-|R|-"],
        [[crl("Root")]],
        "bad.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")


def test_case_aw(tmp_path):
    """A leaf without SAN evidence for the requested name type fails leaf policy."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "820", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "821", False, ku="digitalSignature", eku="serverAuth", ip="10.0.0.10", aki="R")],
        [[crl("Root")]],
        "missing-dns.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_ax(tmp_path):
    """Certificate identifiers are unique across trust and bundle records."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "830", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Root", "831", False, ku="digitalSignature", eku="serverAuth", dns="dup.example", aki="R"),
            cert("L", "other", "Root", "832", False, ku="digitalSignature", eku="serverAuth", dns="dup.example", aki="R"),
        ],
        [[crl("Root")]],
        "dup.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")


def test_case_ay(tmp_path):
    """Malformed booleans are rejected before otherwise admissible evidence is considered."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "840", True, ku="keyCertSign")],
        ["cert|L|leaf|Root|841|maybe|1600000000|1800000000|digitalSignature|serverAuth|bool.example|-|-|-|R|-"],
        [[crl("Root")]],
        "bool.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")


def test_case_az(tmp_path):
    """A valid sibling path can admit the certificate despite a failing cross-signed issuer."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "850", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Inter", "853", False, ku="digitalSignature", eku="serverAuth", dns="cross.example", aki="M-good"),
            cert("M-bad", "Inter", "Root", "851", True, ku="keyCertSign", permit="other.example", aki="R"),
            cert("M-good", "Inter", "Root", "852", True, ku="keyCertSign", permit="example", aki="R"),
        ],
        [[crl("Inter"), crl("Root")]],
        "cross.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_ba(tmp_path):
    """A leaf policy can be carried through successive issuer policy mappings."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "900", True, ku="keyCertSign", policies="rootWeb")],
        [
            cert("L", "leaf", "Mid", "903", False, ku="digitalSignature", eku="serverAuth", dns="mapped.example", aki="M", policies="leafWeb"),
            cert("M", "Mid", "Upper", "902", True, ku="keyCertSign", aki="U", path_len=0, policies="midWeb", policy_maps="leafWeb>midWeb"),
            cert("U", "Upper", "Root", "901", True, ku="keyCertSign", aki="R", path_len=1, policies="rootWeb", policy_maps="midWeb>rootWeb"),
        ],
        [[crl("Mid")], [crl("Upper")], [crl("Root")]],
        "mapped.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_bb(tmp_path):
    """Policy mapping rejects a path when an intermediate cannot carry the leaf policy."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "910", True, ku="keyCertSign", policies="rootWeb")],
        [
            cert("L", "leaf", "Mid", "913", False, ku="digitalSignature", eku="serverAuth", dns="policy-gap.example", aki="M", policies="leafWeb"),
            cert("M", "Mid", "Upper", "912", True, ku="keyCertSign", aki="U", path_len=0, policies="midMail", policy_maps="leafWeb>midWeb"),
            cert("U", "Upper", "Root", "911", True, ku="keyCertSign", aki="R", path_len=1, policies="rootWeb", policy_maps="midWeb>rootWeb"),
        ],
        [[crl("Mid")], [crl("Upper")], [crl("Root")]],
        "policy-gap.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_bc(tmp_path):
    """The anyPolicy marker authorizes a carried policy at one issuer without changing later mappings."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "920", True, ku="keyCertSign", policies="rootWeb")],
        [
            cert("L", "leaf", "Mid", "923", False, ku="digitalSignature", eku="serverAuth", dns="anypolicy.example", aki="M", policies="leafWeb"),
            cert("M", "Mid", "Upper", "922", True, ku="keyCertSign", aki="U", path_len=0, policies="anyPolicy"),
            cert("U", "Upper", "Root", "921", True, ku="keyCertSign", aki="R", path_len=1, policies="rootWeb", policy_maps="leafWeb>rootWeb"),
        ],
        [[crl("Mid")], [crl("Upper")], [crl("Root")]],
        "anypolicy.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_bd(tmp_path):
    """Malformed policy-map entries reject the input before path evaluation."""
    bad_map = "cert|M|Mid|Root|931|true|1600000000|1800000000|keyCertSign|-|-|-|-|-|R|-|serverTLS|serverTLS=anchor"
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "930", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "932", False, ku="digitalSignature", eku="serverAuth", dns="bad-map.example", aki="M"),
            bad_map,
        ],
        [[crl("Mid"), crl("Root")]],
        "bad-map.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")


def test_case_be(tmp_path):
    """Issuer-qualified CRL entries revoke only the path using that issuer id."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "940", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Shared", "943", False, ku="digitalSignature", eku="serverAuth", dns="qualified-crl.example", aki="M-good"),
            cert("M-bad", "Shared", "Root", "941", True, ku="keyCertSign", aki="R"),
            cert("M-good", "Shared", "Root", "942", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Shared", serials="943@M-bad"), crl("Root")]],
        "qualified-crl.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_bf(tmp_path):
    """Issuer-qualified CRL entries revoke the matching cross-signed path."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "950", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Shared", "953", False, ku="digitalSignature", eku="serverAuth", dns="qualified-revoked.example", aki="M-bad"),
            cert("M-bad", "Shared", "Root", "951", True, ku="keyCertSign", aki="R"),
            cert("M-good", "Shared", "Root", "952", True, ku="keyCertSign", aki="R"),
        ],
        [[crl("Shared", serials="953@M-bad"), crl("Root")]],
        "qualified-revoked.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:REVOKED", "")


def test_case_bg(tmp_path):
    """Issuer requirement tokens choose among same-subject issuers without becoming policies."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "960", True, ku="keyCertSign", policies="anchorWeb", policy_maps="midWeb>anchorWeb")],
        [
            cert("L", "leaf", "Shared", "963", False, ku="digitalSignature", eku="serverAuth", dns="pinned.example", aki="M-good", policies="leafWeb,requireIssuer:M-good"),
            cert("M-bad", "Shared", "Root", "961", True, ku="keyCertSign", aki="R", policies="anchorWeb", policy_maps="leafWeb>anchorWeb"),
            cert("M-good", "Shared", "Root", "962", True, ku="keyCertSign", aki="R", policies="midWeb", policy_maps="leafWeb>midWeb"),
        ],
        [[crl("Shared"), crl("Root")]],
        "pinned.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_bh(tmp_path):
    """Malformed issuer-qualified CRL serial entries reject before admission."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "970", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "971", False, ku="digitalSignature", eku="serverAuth", dns="bad-qualified.example", aki="R")],
        [[crl("Root", serials="971@")]],
        "bad-qualified.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")


def test_case_bi(tmp_path):
    """Issuer requirement tokens on intermediates constrain the parent certificate id."""
    res = run_case(
        tmp_path,
        [
            cert("R1", "Root", "Root", "980", True, ku="keyCertSign"),
            cert("R2", "Root", "Root", "981", True, ku="keyCertSign"),
        ],
        [
            cert("L", "leaf", "Mid", "983", False, ku="digitalSignature", eku="serverAuth", dns="issuer-chain.example", aki="M"),
            cert("M", "Mid", "Root", "982", True, ku="keyCertSign", aki="R1", policies="serverTLS,requireIssuer:R2"),
        ],
        [[crl("Mid"), crl("Root")]],
        "issuer-chain.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:CA_POLICY", "")


def test_case_bj(tmp_path):
    """Any matching issuer requirement alternative on an intermediate permits the path."""
    res = run_case(
        tmp_path,
        [cert("R2", "Root", "Root", "990", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "992", False, ku="digitalSignature", eku="serverAuth", dns="issuer-alt.example", aki="M"),
            cert("M", "Mid", "Root", "991", True, ku="keyCertSign", aki="R2", policies="serverTLS,requireIssuer:R1,requireIssuer:R2"),
        ],
        [[crl("Mid"), crl("Root")]],
        "issuer-alt.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_bk(tmp_path):
    """Candidate path construction rejects cycles without falling back to subject-only links."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "1000", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "NodeA", "1003", False, ku="digitalSignature", eku="serverAuth", dns="cycle.example", aki="A"),
            cert("A", "NodeA", "NodeB", "1002", True, ku="keyCertSign", aki="B"),
            cert("B", "NodeB", "NodeA", "1001", True, ku="keyCertSign", aki="A"),
        ],
        [[crl("NodeA"), crl("NodeB"), crl("Root")]],
        "cycle.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:NO_PATH", "")


def test_case_bl(tmp_path):
    """Same-number selected full CRLs are cumulative, not file-order alternatives."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "1010", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "1011", False, ku="digitalSignature", eku="serverAuth", dns="tie-crl.example", aki="R")],
        [
            [crl("Root", number=5, serials="-")],
            [crl("Root", number=5, serials="1011")],
        ],
        "tie-crl.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:REVOKED", "")


def test_case_bm(tmp_path):
    """Deltas attach to all tied greatest full CRLs through the shared base number."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "1020", True, ku="keyCertSign")],
        [cert("L", "leaf", "Root", "1021", False, ku="digitalSignature", eku="serverAuth", dns="tie-delta.example", aki="R")],
        [
            [crl("Root", number=8, serials="-")],
            [crl("Root", number=8, serials="1020")],
            [crl("Root", number=9, base=8, serials="1021")],
        ],
        "tie-delta.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:REVOKED", "")


def test_case_bn(tmp_path):
    """Issuer-scoped policy maps can carry a policy through the matching cross-signed issuer."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "1030", True, ku="keyCertSign", policies="midWeb")],
        [
            cert("L", "leaf", "Shared", "1033", False, ku="digitalSignature", eku="serverAuth", dns="scoped-map.example", aki="M-good", policies="tenantWeb"),
            cert("M-bad", "Shared", "Root", "1031", True, ku="keyCertSign", aki="R", policies="midWeb", policy_maps="tenantWeb>midWeb@M-good"),
            cert("M-good", "Shared", "Root", "1032", True, ku="keyCertSign", aki="R", policies="midWeb", policy_maps="tenantWeb>midWeb@M-good"),
        ],
        [[crl("Shared"), crl("Root")]],
        "scoped-map.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (0, "ACCEPT", "")


def test_case_bo(tmp_path):
    """Issuer-scoped policy maps do not apply through a same-subject issuer with another id."""
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "1040", True, ku="keyCertSign", policies="midWeb")],
        [
            cert("L", "leaf", "Shared", "1043", False, ku="digitalSignature", eku="serverAuth", dns="wrong-scope.example", aki="M-bad", policies="tenantWeb"),
            cert("M-bad", "Shared", "Root", "1041", True, ku="keyCertSign", aki="R", policies="midWeb", policy_maps="tenantWeb>midWeb@M-good"),
            cert("M-good", "Shared", "Root", "1042", True, ku="keyCertSign", aki="R", policies="midWeb", policy_maps="tenantWeb>midWeb@M-good"),
        ],
        [[crl("Shared"), crl("Root")]],
        "wrong-scope.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:LEAF_POLICY", "")


def test_case_bp(tmp_path):
    """Malformed scoped policy-map issuer ids reject the input before path evaluation."""
    bad_map = "cert|M|Mid|Root|1051|true|1600000000|1800000000|keyCertSign|-|-|-|-|-|R|-|midWeb|tenantWeb>midWeb@"
    res = run_case(
        tmp_path,
        [cert("R", "Root", "Root", "1050", True, ku="keyCertSign")],
        [
            cert("L", "leaf", "Mid", "1052", False, ku="digitalSignature", eku="serverAuth", dns="bad-scope.example", aki="M", policies="tenantWeb"),
            bad_map,
        ],
        [[crl("Mid"), crl("Root")]],
        "bad-scope.example",
    )
    assert (res.returncode, res.stdout.strip(), res.stderr) == (1, "REJECT:MALFORMED", "")
