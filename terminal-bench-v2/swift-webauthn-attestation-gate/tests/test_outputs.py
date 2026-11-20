"""Behavioral verifier for the Swift WebAuthn attestation gate."""

import base64
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

BIN = Path("/app/webauthngate/bin/webauthngate")
BUILD = Path("/app/webauthngate/scripts/build.sh")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64der(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def canonical_client(
    challenge: str,
    origin: str = "https://login.example",
    cross=False,
    ctype="webauthn.create",
) -> bytes:
    obj = {"challenge": challenge, "origin": origin, "type": ctype}
    if cross is not None:
        obj["crossOrigin"] = cross
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def auth_data(
    rp_id: str,
    flags: int,
    counter: int,
    aaguid_hex: str,
    cred: bytes,
    extensions: dict | None = None,
) -> bytes:
    data = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([flags])
        + counter.to_bytes(4, "big")
        + bytes.fromhex(aaguid_hex)
        + len(cred).to_bytes(2, "big")
        + cred
    )
    if extensions is not None:
        ext = json.dumps(extensions, sort_keys=True, separators=(",", ":")).encode()
        data += len(ext).to_bytes(2, "big") + ext
    return data


def run(cmd, input_data=None, check=True):
    return subprocess.run(
        cmd, input=input_data, check=check, text=False, capture_output=True
    )


def make_ca(tmp: Path, name: str = "trusted", leaf_ext_text: str | None = None):
    root_key = tmp / f"{name}-root.key"
    root_pem = tmp / f"{name}-root.pem"
    leaf_key = tmp / f"{name}-leaf.key"
    leaf_csr = tmp / f"{name}-leaf.csr"
    leaf_pem = tmp / f"{name}-leaf.pem"
    leaf_der = tmp / f"{name}-leaf.der"
    root_cnf = tmp / f"{name}-root.cnf"
    leaf_ext = tmp / f"{name}-leaf.ext"
    root_cnf.write_text(
        "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=TB2 Root\n[v3_ca]\nbasicConstraints=critical,CA:TRUE,pathlen:1\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n",
        encoding="utf-8",
    )
    leaf_ext.write_text(
        leaf_ext_text
        or "[v3_leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
        encoding="utf-8",
    )
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "3650",
            "-subj",
            "/CN=TB2 Root",
            "-keyout",
            str(root_key),
            "-out",
            str(root_pem),
            "-config",
            str(root_cnf),
            "-extensions",
            "v3_ca",
        ]
    )
    run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=TB2 Attestation",
            "-keyout",
            str(leaf_key),
            "-out",
            str(leaf_csr),
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(root_pem),
            "-CAkey",
            str(root_key),
            "-CAcreateserial",
            "-out",
            str(leaf_pem),
            "-days",
            "365",
            "-sha256",
            "-extfile",
            str(leaf_ext),
            "-extensions",
            "v3_leaf",
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-in",
            str(leaf_pem),
            "-outform",
            "DER",
            "-out",
            str(leaf_der),
        ]
    )
    return {"root_pem": root_pem, "leaf_key": leaf_key, "leaf_der": leaf_der}


def make_ec_ca(tmp: Path, name: str = "ec-trusted"):
    root_key = tmp / f"{name}-root.key"
    root_pem = tmp / f"{name}-root.pem"
    leaf_key = tmp / f"{name}-leaf.key"
    leaf_csr = tmp / f"{name}-leaf.csr"
    leaf_pem = tmp / f"{name}-leaf.pem"
    leaf_der = tmp / f"{name}-leaf.der"
    root_cnf = tmp / f"{name}-root.cnf"
    leaf_ext = tmp / f"{name}-leaf.ext"
    root_cnf.write_text(
        "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=TB2 EC Root\n[v3_ca]\nbasicConstraints=critical,CA:TRUE,pathlen:1\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n",
        encoding="utf-8",
    )
    leaf_ext.write_text(
        "[v3_leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
        encoding="utf-8",
    )
    run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(root_key)])
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-key",
            str(root_key),
            "-sha256",
            "-days",
            "3650",
            "-subj",
            "/CN=TB2 EC Root",
            "-out",
            str(root_pem),
            "-config",
            str(root_cnf),
            "-extensions",
            "v3_ca",
        ]
    )
    run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(leaf_key)])
    run(["openssl", "req", "-new", "-key", str(leaf_key), "-subj", "/CN=TB2 EC Attestation", "-out", str(leaf_csr)])
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(root_pem),
            "-CAkey",
            str(root_key),
            "-CAcreateserial",
            "-out",
            str(leaf_pem),
            "-days",
            "365",
            "-sha256",
            "-extfile",
            str(leaf_ext),
            "-extensions",
            "v3_leaf",
        ]
    )
    run(["openssl", "x509", "-in", str(leaf_pem), "-outform", "DER", "-out", str(leaf_der)])
    return {"root_pem": root_pem, "leaf_key": leaf_key, "leaf_der": leaf_der}


def make_chain(tmp: Path, name: str = "chain"):
    root_key = tmp / f"{name}-root.key"
    root_pem = tmp / f"{name}-root.pem"
    int_key = tmp / f"{name}-int.key"
    int_csr = tmp / f"{name}-int.csr"
    int_pem = tmp / f"{name}-int.pem"
    int_der = tmp / f"{name}-int.der"
    leaf_key = tmp / f"{name}-leaf.key"
    leaf_csr = tmp / f"{name}-leaf.csr"
    leaf_pem = tmp / f"{name}-leaf.pem"
    leaf_der = tmp / f"{name}-leaf.der"
    root_cnf = tmp / f"{name}-root.cnf"
    int_ext = tmp / f"{name}-int.ext"
    leaf_ext = tmp / f"{name}-leaf.ext"
    root_cnf.write_text(
        "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=TB2 Root\n[v3_ca]\nbasicConstraints=critical,CA:TRUE,pathlen:1\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n",
        encoding="utf-8",
    )
    int_ext.write_text(
        "[v3_int]\nbasicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
        encoding="utf-8",
    )
    leaf_ext.write_text(
        "[v3_leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
        encoding="utf-8",
    )
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "3650",
            "-subj",
            "/CN=TB2 Root",
            "-keyout",
            str(root_key),
            "-out",
            str(root_pem),
            "-config",
            str(root_cnf),
            "-extensions",
            "v3_ca",
        ]
    )
    run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=TB2 Intermediate",
            "-keyout",
            str(int_key),
            "-out",
            str(int_csr),
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(int_csr),
            "-CA",
            str(root_pem),
            "-CAkey",
            str(root_key),
            "-CAcreateserial",
            "-out",
            str(int_pem),
            "-days",
            "1200",
            "-sha256",
            "-extfile",
            str(int_ext),
            "-extensions",
            "v3_int",
        ]
    )
    run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=TB2 Attestation",
            "-keyout",
            str(leaf_key),
            "-out",
            str(leaf_csr),
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(int_pem),
            "-CAkey",
            str(int_key),
            "-CAcreateserial",
            "-out",
            str(leaf_pem),
            "-days",
            "800",
            "-sha256",
            "-extfile",
            str(leaf_ext),
            "-extensions",
            "v3_leaf",
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-in",
            str(int_pem),
            "-outform",
            "DER",
            "-out",
            str(int_der),
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-in",
            str(leaf_pem),
            "-outform",
            "DER",
            "-out",
            str(leaf_der),
        ]
    )
    return {
        "root_pem": root_pem,
        "leaf_key": leaf_key,
        "leaf_der": leaf_der,
        "chain_der": [int_der],
    }


def sign(tmp: Path, key: Path, payload: bytes, stem: str) -> bytes:
    data_file = tmp / f"{stem}.signed"
    sig_file = tmp / f"{stem}.sig"
    data_file.write_bytes(payload)
    run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(key),
            "-out",
            str(sig_file),
            str(data_file),
        ]
    )
    return sig_file.read_bytes()


def evidence(
    tmp: Path,
    cert,
    *,
    eid: str,
    rp_id="login.example",
    challenge="c",
    cred=b"cred-1",
    counter=1,
    flags=0x45,
    aaguid="00112233445566778899aabbccddeeff",
    origin="https://login.example",
    ctype="webauthn.create",
    cross=False,
    fmt="packed",
    alg=-257,
    tamper_sig=False,
    canonical=True,
    x5c_paths=None,
    extensions=None,
):
    client = canonical_client(challenge, origin, cross, ctype)
    if not canonical:
        client = json.dumps(
            {
                "type": "webauthn.create",
                "origin": origin,
                "challenge": challenge,
                "crossOrigin": False,
            },
            indent=1,
        ).encode()
    if extensions is not None:
        flags |= 0x80
    ad = auth_data(rp_id, flags, counter, aaguid, cred, extensions=extensions)
    sig = sign(tmp, cert["leaf_key"], ad + hashlib.sha256(client).digest(), eid)
    if tamper_sig:
        sig = sig[:-1] + bytes([sig[-1] ^ 0x55])
    if x5c_paths is None:
        x5c_paths = [cert["leaf_der"]] + cert.get("chain_der", [])
    return {
        "id": eid,
        "credentialId": b64u(cred),
        "clientDataJSON": b64u(client),
        "attestationObject": {
            "fmt": fmt,
            "authData": b64u(ad),
            "attStmt": {
                "alg": alg,
                "sig": b64u(sig),
                "x5c": [b64der(path) for path in x5c_paths],
            },
        },
    }


def write_case(tmp: Path, policy_extra=None, state_extra=None):
    cert = make_ca(tmp, "trusted")
    other = make_ca(tmp, "other")
    evid_dir = tmp / "evidence"
    evid_dir.mkdir()
    policy = {
        "rpId": "login.example",
        "origins": ["https://login.example"],
        "challenges": [
            "alpha",
            "beta",
            "gamma",
            "delta",
            "epsilon",
            "zeta",
            "eta",
            "theta",
        ],
        "trustedRoots": [cert["root_pem"].read_text(encoding="utf-8")],
        "allowedAAGUIDs": ["00112233445566778899aabbccddeeff"],
        "requireUserVerification": True,
        "now": int(time.time()),
    }
    if policy_extra:
        policy.update(policy_extra)
    state = {"credentials": {}, "consumedChallenges": ["old"]}
    if state_extra:
        state.update(state_extra)
    policy_path = tmp / "policy.json"
    state_path = tmp / "state.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    return cert, other, evid_dir, policy_path, state_path


def invoke(policy: Path, evid_dir: Path, state: Path, out: Path):
    run([str(BUILD)], check=True)
    return subprocess.run(
        [
            str(BIN),
            "--policy",
            str(policy),
            "--evidence",
            str(evid_dir),
            "--state",
            str(state),
            "--out",
            str(out),
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def digest_tree(paths):
    result = {}
    for path in paths:
        if path.is_dir():
            for child in sorted(path.glob("*.json")):
                result[str(child)] = hashlib.sha256(child.read_bytes()).hexdigest()
        else:
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_mixed_dossier_enforces_crypto_policy_and_preserves_inputs():
    """A mixed archive admits only evidence satisfying RP, UV, trust, signature, and counter policy without mutating inputs."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cred = b"credential-A"
        cert, other, evid_dir, policy, state = write_case(
            tmp, state_extra={"credentials": {b64u(cred): 9}}
        )
        cases = [
            (
                "01-ok.json",
                evidence(tmp, cert, eid="ok", challenge="alpha", cred=cred, counter=10),
            ),
            (
                "02-rp.json",
                evidence(
                    tmp,
                    cert,
                    eid="bad-rp",
                    rp_id="evil.example",
                    challenge="beta",
                    cred=b"credential-B",
                    counter=1,
                ),
            ),
            (
                "03-uv.json",
                evidence(
                    tmp,
                    cert,
                    eid="no-uv",
                    challenge="gamma",
                    cred=b"credential-C",
                    counter=1,
                    flags=0x41,
                ),
            ),
            (
                "04-trust.json",
                evidence(
                    tmp,
                    other,
                    eid="untrusted",
                    challenge="delta",
                    cred=b"credential-D",
                    counter=1,
                ),
            ),
            (
                "05-sig.json",
                evidence(
                    tmp,
                    cert,
                    eid="bad-sig",
                    challenge="epsilon",
                    cred=b"credential-E",
                    counter=1,
                    tamper_sig=True,
                ),
            ),
            (
                "06-counter.json",
                evidence(
                    tmp, cert, eid="rollback", challenge="zeta", cred=cred, counter=8
                ),
            ),
        ]
        for name, body in cases:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        before = digest_tree([policy, state, evid_dir])
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        assert proc.stdout == ""
        assert digest_tree([policy, state, evid_dir]) == before
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["summary"] == {"accepted": 1, "highestCounter": 10, "rejected": 5}
        assert report["accepted"] == [
            {
                "aaguid": "00112233445566778899aabbccddeeff",
                "credentialId": b64u(cred),
                "id": "ok",
                "signCount": 10,
            }
        ]
        assert report["rejected"] == [
            {"id": "bad-rp", "reason": "rpId"},
            {"id": "no-uv", "reason": "userVerification"},
            {"id": "untrusted", "reason": "trust"},
            {"id": "bad-sig", "reason": "signature"},
            {"id": "rollback", "reason": "counter"},
        ]
        assert report["nextState"]["credentials"][b64u(cred)] == 10
        assert "alpha" in report["nextState"]["consumedChallenges"]


def test_clean_batch_reports_deterministic_replay_state():
    """Accepted registrations advance per-credential counters and consumed challenges only in the report's next state."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cert, _, evid_dir, policy, state = write_case(tmp)
        cred = b"credential-clean"
        for name, challenge, counter in [
            ("01.json", "alpha", 1),
            ("02.json", "beta", 2),
        ]:
            (evid_dir / name).write_text(
                json.dumps(
                    evidence(
                        tmp,
                        cert,
                        eid=name,
                        challenge=challenge,
                        cred=cred,
                        counter=counter,
                    ),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        out1 = tmp / "report1.json"
        proc = invoke(policy, evid_dir, state, out1)
        assert proc.returncode == 0
        assert proc.stdout == ""
        report1 = json.loads(out1.read_text(encoding="utf-8"))
        assert report1["summary"] == {"accepted": 2, "highestCounter": 2, "rejected": 0}
        assert report1["nextState"]["credentials"] == {b64u(cred): 2}
        assert report1["nextState"]["consumedChallenges"] == ["alpha", "beta", "old"]
        out2 = tmp / "report2.json"
        proc2 = invoke(policy, evid_dir, state, out2)
        assert proc2.returncode == 0
        assert proc2.stdout == ""
        assert out1.read_bytes() == out2.read_bytes()
        assert json.loads(state.read_text(encoding="utf-8")) == {
            "consumedChallenges": ["old"],
            "credentials": {},
        }


def test_es256_attestation_requires_matching_transaction_extension():
    """ES256 attestations must use EC trust roots and satisfy per-challenge txAuthSimple extension policy."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rsa_cert, _, evid_dir, policy, state = write_case(
            tmp,
            policy_extra={
                "requiredTxAuth": {
                    "alpha": "wire-transfer:ops:1000",
                    "beta": "wire-transfer:ops:2000",
                    "gamma": "wire-transfer:ops:3000",
                }
            },
        )
        ec_cert = make_ec_ca(tmp, "trusted-ec")
        policy_obj = json.loads(policy.read_text(encoding="utf-8"))
        policy_obj["trustedRoots"].append(ec_cert["root_pem"].read_text(encoding="utf-8"))
        policy.write_text(json.dumps(policy_obj, sort_keys=True), encoding="utf-8")
        cases = [
            (
                "01-ec-ok.json",
                evidence(
                    tmp,
                    ec_cert,
                    eid="ec-ok",
                    challenge="alpha",
                    cred=b"cred-ec-ok",
                    counter=1,
                    alg=-7,
                    extensions={"txAuthSimple": "wire-transfer:ops:1000", "uvm": [[1, 2, 3]]},
                ),
            ),
            (
                "02-missing-tx.json",
                evidence(
                    tmp,
                    ec_cert,
                    eid="missing-tx",
                    challenge="beta",
                    cred=b"cred-no-tx",
                    counter=1,
                    alg=-7,
                ),
            ),
            (
                "03-wrong-tx.json",
                evidence(
                    tmp,
                    ec_cert,
                    eid="wrong-tx",
                    challenge="gamma",
                    cred=b"cred-wrong-tx",
                    counter=1,
                    alg=-7,
                    extensions={"txAuthSimple": "wire-transfer:ops:9999"},
                ),
            ),
            (
                "04-alg-key-mismatch.json",
                evidence(
                    tmp,
                    rsa_cert,
                    eid="rsa-as-es256",
                    challenge="delta",
                    cred=b"cred-rsa-es",
                    counter=1,
                    alg=-7,
                ),
            ),
        ]
        for name, body in cases:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["accepted"] == [
            {
                "aaguid": "00112233445566778899aabbccddeeff",
                "credentialId": b64u(b"cred-ec-ok"),
                "id": "ec-ok",
                "signCount": 1,
            }
        ]
        assert report["rejected"] == [
            {"id": "missing-tx", "reason": "client"},
            {"id": "wrong-tx", "reason": "client"},
            {"id": "rsa-as-es256", "reason": "attestationFormat"},
        ]
        assert report["nextState"]["consumedChallenges"] == ["alpha", "old"]


def test_canonical_client_attestation_format_and_credential_edges():
    """Canonical client JSON, packed format, and credential ID binding are independently enforced."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cert, _, evid_dir, policy, state = write_case(tmp)
        noncanonical = evidence(
            tmp,
            cert,
            eid="noncanonical",
            challenge="alpha",
            cred=b"cred-noncanon",
            counter=1,
            canonical=False,
        )
        not_packed = evidence(
            tmp,
            cert,
            eid="not-packed",
            challenge="beta",
            cred=b"cred-fmt",
            counter=1,
            fmt="none",
        )
        bad_alg = evidence(
            tmp,
            cert,
            eid="bad-alg",
            challenge="gamma",
            cred=b"cred-alg",
            counter=1,
            alg=-7,
        )
        mismatch = evidence(
            tmp,
            cert,
            eid="cred-mismatch",
            challenge="theta",
            cred=b"real-credential",
            counter=1,
        )
        mismatch["credentialId"] = b64u(b"reported-credential")
        malformed = evidence(
            tmp,
            cert,
            eid="malformed",
            challenge="eta",
            cred=b"cred-malformed",
            counter=1,
        )
        malformed["attestationObject"]["authData"] = b64u(b"too-short")
        for name, body in [
            ("01.json", noncanonical),
            ("02.json", not_packed),
            ("03.json", bad_alg),
            ("04.json", mismatch),
            ("05.json", malformed),
        ]:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["accepted"] == []
        assert report["summary"] == {"accepted": 0, "highestCounter": 0, "rejected": 5}
        assert report["rejected"] == [
            {"id": "noncanonical", "reason": "client"},
            {"id": "not-packed", "reason": "attestationFormat"},
            {"id": "bad-alg", "reason": "attestationFormat"},
            {"id": "cred-mismatch", "reason": "credential"},
            {"id": "malformed", "reason": "malformed"},
        ]


def test_client_challenge_origin_type_and_replay_rules():
    """Client-data policy rejects unknown, consumed, reused, wrong-origin, wrong-type, and cross-origin challenges."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cert, _, evid_dir, policy, state = write_case(tmp)
        cases = [
            (
                "01-ok.json",
                evidence(
                    tmp,
                    cert,
                    eid="first",
                    challenge="alpha",
                    cred=b"cred-first",
                    counter=1,
                ),
            ),
            (
                "02-reuse.json",
                evidence(
                    tmp,
                    cert,
                    eid="reuse",
                    challenge="alpha",
                    cred=b"cred-reuse",
                    counter=1,
                ),
            ),
            (
                "03-unknown.json",
                evidence(
                    tmp,
                    cert,
                    eid="unknown",
                    challenge="not-in-policy",
                    cred=b"cred-unknown",
                    counter=1,
                ),
            ),
            (
                "04-consumed.json",
                evidence(
                    tmp, cert, eid="old", challenge="old", cred=b"cred-old", counter=1
                ),
            ),
            (
                "05-origin.json",
                evidence(
                    tmp,
                    cert,
                    eid="origin",
                    challenge="beta",
                    origin="https://evil.example",
                    cred=b"cred-origin",
                    counter=1,
                ),
            ),
            (
                "06-type.json",
                evidence(
                    tmp,
                    cert,
                    eid="type",
                    challenge="gamma",
                    ctype="webauthn.get",
                    cred=b"cred-type",
                    counter=1,
                ),
            ),
            (
                "07-cross.json",
                evidence(
                    tmp,
                    cert,
                    eid="cross",
                    challenge="delta",
                    cross=True,
                    cred=b"cred-cross",
                    counter=1,
                ),
            ),
        ]
        for name, body in cases:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert [row["id"] for row in report["accepted"]] == ["first"]
        assert report["rejected"] == [
            {"id": "reuse", "reason": "client"},
            {"id": "unknown", "reason": "client"},
            {"id": "old", "reason": "client"},
            {"id": "origin", "reason": "client"},
            {"id": "type", "reason": "client"},
            {"id": "cross", "reason": "client"},
        ]
        assert report["nextState"]["consumedChallenges"] == ["alpha", "old"]


def test_absent_cross_origin_and_optional_user_verification_admit_valid_evidence():
    """Valid evidence may omit crossOrigin and may omit the UV bit when policy does not require user verification."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cert, _, evid_dir, policy, state = write_case(
            tmp, policy_extra={"requireUserVerification": False}
        )
        (evid_dir / "01-no-cross.json").write_text(
            json.dumps(
                evidence(
                    tmp,
                    cert,
                    eid="no-cross",
                    challenge="alpha",
                    cred=b"cred-no-cross",
                    counter=1,
                    cross=None,
                ),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (evid_dir / "02-no-uv.json").write_text(
            json.dumps(
                evidence(
                    tmp,
                    cert,
                    eid="no-uv-required",
                    challenge="beta",
                    cred=b"cred-no-uv",
                    counter=2,
                    flags=0x41,
                ),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["rejected"] == []
        assert report["accepted"] == [
            {
                "aaguid": "00112233445566778899aabbccddeeff",
                "credentialId": b64u(b"cred-no-cross"),
                "id": "no-cross",
                "signCount": 1,
            },
            {
                "aaguid": "00112233445566778899aabbccddeeff",
                "credentialId": b64u(b"cred-no-uv"),
                "id": "no-uv-required",
                "signCount": 2,
            },
        ]
        assert report["summary"] == {"accepted": 2, "highestCounter": 2, "rejected": 0}


def test_attested_credential_data_flag_is_required():
    """Authenticator data without the attested-credential-data flag is rejected before trust checks."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cert, _, evid_dir, policy, state = write_case(tmp)
        (evid_dir / "01-no-at.json").write_text(
            json.dumps(
                evidence(
                    tmp,
                    cert,
                    eid="no-at",
                    challenge="alpha",
                    flags=0x05,
                    cred=b"cred-no-at",
                    counter=1,
                ),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["accepted"] == []
        assert report["rejected"] == [{"id": "no-at", "reason": "attestationFormat"}]
        assert report["summary"] == {"accepted": 0, "highestCounter": 0, "rejected": 1}


def test_user_presence_and_allowed_aaguid_are_required():
    """Authenticator data must carry user presence and a locally allowed AAGUID."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cert, _, evid_dir, policy, state = write_case(tmp)
        cases = [
            (
                "01-presence.json",
                evidence(
                    tmp,
                    cert,
                    eid="no-presence",
                    challenge="alpha",
                    flags=0x44,
                    cred=b"cred-presence",
                    counter=1,
                ),
            ),
            (
                "02-aaguid.json",
                evidence(
                    tmp,
                    cert,
                    eid="bad-aaguid",
                    challenge="beta",
                    aaguid="ffeeddccbbaa99887766554433221100",
                    cred=b"cred-aaguid",
                    counter=1,
                ),
            ),
        ]
        for name, body in cases:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["accepted"] == []
        assert report["rejected"] == [
            {"id": "no-presence", "reason": "userPresence"},
            {"id": "bad-aaguid", "reason": "trust"},
        ]


def test_leaf_certificate_constraints_expiry_and_intermediate_chain_validation():
    """Local trust rejects CA leaves, missing signing usage, expired leaves, and omitted intermediates while accepting a complete chain."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ca_leaf = make_ca(
            tmp,
            "ca-leaf",
            "[v3_leaf]\nbasicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,digitalSignature,keyCertSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
        )
        no_dsig = make_ca(
            tmp,
            "no-dsig",
            "[v3_leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,keyEncipherment\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n",
        )
        expired = make_ca(tmp, "expired")
        chain = make_chain(tmp, "intermediate")
        evid_dir = tmp / "evidence"
        evid_dir.mkdir()
        future = int(time.time()) + 500 * 24 * 60 * 60
        policy = {
            "rpId": "login.example",
            "origins": ["https://login.example"],
            "challenges": ["alpha", "beta", "gamma", "delta", "epsilon"],
            "trustedRoots": [
                ca_leaf["root_pem"].read_text(encoding="utf-8"),
                no_dsig["root_pem"].read_text(encoding="utf-8"),
                expired["root_pem"].read_text(encoding="utf-8"),
                chain["root_pem"].read_text(encoding="utf-8"),
            ],
            "allowedAAGUIDs": ["00112233445566778899aabbccddeeff"],
            "requireUserVerification": True,
            "now": future,
        }
        state_obj = {"credentials": {}, "consumedChallenges": []}
        policy_path = tmp / "policy.json"
        state_path = tmp / "state.json"
        policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
        state_path.write_text(json.dumps(state_obj, sort_keys=True), encoding="utf-8")
        cases = [
            (
                "01-ca.json",
                evidence(
                    tmp,
                    ca_leaf,
                    eid="ca-leaf",
                    challenge="alpha",
                    cred=b"cred-ca",
                    counter=1,
                ),
            ),
            (
                "02-ku.json",
                evidence(
                    tmp,
                    no_dsig,
                    eid="no-dsig",
                    challenge="beta",
                    cred=b"cred-ku",
                    counter=1,
                ),
            ),
            (
                "03-expired.json",
                evidence(
                    tmp,
                    expired,
                    eid="expired",
                    challenge="gamma",
                    cred=b"cred-expired",
                    counter=1,
                ),
            ),
            (
                "04-missing-int.json",
                evidence(
                    tmp,
                    chain,
                    eid="missing-intermediate",
                    challenge="delta",
                    cred=b"cred-missing",
                    counter=1,
                    x5c_paths=[chain["leaf_der"]],
                ),
            ),
            (
                "05-chain.json",
                evidence(
                    tmp,
                    chain,
                    eid="complete-chain",
                    challenge="epsilon",
                    cred=b"cred-chain",
                    counter=1,
                ),
            ),
        ]
        for name, body in cases:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        out = tmp / "report.json"
        proc = invoke(policy_path, evid_dir, state_path, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert [row["id"] for row in report["accepted"]] == ["complete-chain"]
        assert report["rejected"] == [
            {"id": "ca-leaf", "reason": "trust"},
            {"id": "no-dsig", "reason": "trust"},
            {"id": "expired", "reason": "trust"},
            {"id": "missing-intermediate", "reason": "trust"},
        ]


def test_public_reason_precedence_for_overlapping_failures():
    """When one evidence object violates several rules, the report uses the documented first reason."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cred_real = b"cred-real"
        cred_reported = b"cred-reported"
        cert, other, evid_dir, policy, state = write_case(
            tmp, state_extra={"credentials": {b64u(cred_reported): 5}}
        )
        client_rp = evidence(
            tmp,
            cert,
            eid="client-rp",
            challenge="not-in-policy",
            rp_id="evil.example",
            cred=b"cred-client-rp",
            counter=1,
        )
        rp_presence = evidence(
            tmp,
            cert,
            eid="rp-presence",
            rp_id="evil.example",
            flags=0x44,
            challenge="alpha",
            cred=b"cred-rp-presence",
            counter=1,
        )
        uv_format = evidence(
            tmp,
            cert,
            eid="uv-format",
            flags=0x41,
            fmt="none",
            challenge="beta",
            cred=b"cred-uv-format",
            counter=1,
        )
        trust_sig = evidence(
            tmp,
            other,
            eid="trust-sig",
            challenge="gamma",
            cred=b"cred-trust-sig",
            counter=1,
            tamper_sig=True,
        )
        sig_cred = evidence(
            tmp,
            cert,
            eid="sig-cred",
            challenge="delta",
            cred=cred_real,
            counter=1,
            tamper_sig=True,
        )
        sig_cred["credentialId"] = b64u(cred_reported)
        cred_counter = evidence(
            tmp,
            cert,
            eid="cred-counter",
            challenge="epsilon",
            cred=cred_real,
            counter=1,
        )
        cred_counter["credentialId"] = b64u(cred_reported)
        for name, body in [
            ("01-client-rp.json", client_rp),
            ("02-rp-presence.json", rp_presence),
            ("03-uv-format.json", uv_format),
            ("04-trust-sig.json", trust_sig),
            ("05-sig-cred.json", sig_cred),
            ("06-cred-counter.json", cred_counter),
        ]:
            (evid_dir / name).write_text(
                json.dumps(body, sort_keys=True), encoding="utf-8"
            )
        out = tmp / "report.json"
        proc = invoke(policy, evid_dir, state, out)
        assert proc.returncode != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["accepted"] == []
        assert report["rejected"] == [
            {"id": "client-rp", "reason": "client"},
            {"id": "rp-presence", "reason": "rpId"},
            {"id": "uv-format", "reason": "userVerification"},
            {"id": "trust-sig", "reason": "trust"},
            {"id": "sig-cred", "reason": "signature"},
            {"id": "cred-counter", "reason": "credential"},
        ]
