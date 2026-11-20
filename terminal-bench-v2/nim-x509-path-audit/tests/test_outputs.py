import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile
import time

TOOL = pathlib.Path("/usr/local/sbin/x509-path-attest")
POLICY = "1.2.3.4.5.7"


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


def write(path, text):
    path.write_text(text, encoding="utf-8")


def key(path):
    run(["openssl", "genrsa", "-out", str(path), "2048"])


def req_config(path):
    write(path, "[req]\nprompt=no\ndistinguished_name=dn\n[dn]\nCN=unused\n")


def make_csr(work, name):
    k = work / f"{name}.key"
    c = work / f"{name}.csr"
    cfg = work / f"{name}.req.cnf"
    key(k)
    req_config(cfg)
    run(["openssl", "req", "-new", "-key", str(k), "-out", str(c), "-subj", f"/CN={name}", "-config", str(cfg)])
    return k, c


def root(work, name):
    k = work / f"{name}.key"
    cert = work / f"{name}.pem"
    cfg = work / f"{name}.cnf"
    key(k)
    write(cfg, """
[req]
prompt=no
distinguished_name=dn
x509_extensions=v3_ca
[dn]
CN=root
[v3_ca]
basicConstraints=critical,CA:true
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always,issuer
""".strip() + "\n")
    run(["openssl", "req", "-new", "-x509", "-days", "3650", "-key", str(k), "-out", str(cert), "-subj", f"/CN={name}", "-config", str(cfg)])
    return k, cert


def sign(work, name, ca_key, ca_cert, serial, ext_text, days=120):
    k, csr = make_csr(work, name)
    cert = work / f"{name}.pem"
    cfg = work / f"{name}.ext.cnf"
    write(cfg, ext_text.strip() + "\n")
    run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key), "-set_serial", str(serial), "-days", str(days), "-out", str(cert), "-extfile", str(cfg), "-extensions", "v3"])
    return k, cert


def int_ext(permitted=None, excluded=None, policy=True, pathlen=1):
    lines = [
        "[v3]",
        f"basicConstraints=critical,CA:true,pathlen:{pathlen}",
        "keyUsage=critical,keyCertSign,cRLSign",
        "subjectKeyIdentifier=hash",
        "authorityKeyIdentifier=keyid,issuer",
    ]
    constraints = []
    if permitted:
        constraints.append(f"permitted;DNS:{permitted}")
    if excluded:
        constraints.append(f"excluded;DNS:{excluded}")
    if constraints:
        lines.append("nameConstraints=critical," + ",".join(constraints))
    if policy:
        lines.append(f"certificatePolicies={POLICY}")
    return "\n".join(lines) + "\n"


def leaf_ext(dns, eku="serverAuth", policy=True):
    lines = [
        "[v3]",
        "basicConstraints=critical,CA:false",
        "keyUsage=critical,digitalSignature,keyEncipherment",
        f"extendedKeyUsage={eku}",
        f"subjectAltName=DNS:{dns}",
    ]
    if policy:
        lines.append(f"certificatePolicies={POLICY}")
    return "\n".join(lines) + "\n"


def cert_serial(cert):
    text = run(["openssl", "x509", "-in", str(cert), "-noout", "-serial"]).stdout.strip()
    return text.split("=", 1)[1]


def cert_subject(cert):
    return run(["openssl", "x509", "-in", str(cert), "-noout", "-subject", "-nameopt", "oneline"]).stdout.strip().split("=", 1)[1]


def make_crl(work, name, issuer_key, issuer_cert, revoked=None):
    ca_dir = work / f"ca-{name}"
    ca_dir.mkdir()
    index = ca_dir / "index.txt"
    serial = ca_dir / "crlnumber"
    write(serial, "1000\n")
    rows = [
        f"R\t30000101000000Z\t250101000000Z\t{cert_serial(cert)}\tunknown\t{cert_subject(cert)}"
        for cert in revoked or []
    ]
    write(index, "\n".join(rows) + ("\n" if rows else ""))
    cfg = ca_dir / "ca.cnf"
    write(cfg, f"""
[ca]
default_ca=ca_default
[ca_default]
database={index}
crlnumber={serial}
default_crl_days=90
default_md=sha256
unique_subject=no
""".strip() + "\n")
    crl = work / f"{name}.crl.pem"
    run(["openssl", "ca", "-batch", "-config", str(cfg), "-gencrl", "-keyfile", str(issuer_key), "-cert", str(issuer_cert), "-out", str(crl)])
    return crl


def make_case(tmp, *, dns="svc.allowed.test", request_dns=None, request_eku="serverAuth", leaf_eku="serverAuth", leaf_policy=True, permitted=None, excluded=None, extra_bad=False, days=120, now_offset=3600, with_intermediate=True, deep_chain=False, revoke_leaf=False, revoke_intermediate=False, decoy_crl=False):
    tmp.mkdir(parents=True, exist_ok=True)
    root_key, root_cert = root(tmp, "root-main")
    crls = []
    if with_intermediate:
        int1_key, int1_cert = sign(tmp, "int-main", root_key, root_cert, 10, int_ext(permitted=permitted, excluded=excluded, pathlen=1 if deep_chain else 0))
        if deep_chain:
            signer_key, signer_cert = sign(tmp, "int-sub", int1_key, int1_cert, 11, int_ext(pathlen=0))
            if revoke_intermediate:
                crls.append(make_crl(tmp, "int1-revokes-sub", int1_key, int1_cert, [signer_cert]))
            crls.append(make_crl(tmp, "root-clear", root_key, root_cert, []))
        else:
            signer_key, signer_cert = int1_key, int1_cert
            if revoke_intermediate:
                crls.append(make_crl(tmp, "root-revokes-int", root_key, root_cert, [signer_cert]))
    else:
        signer_key, signer_cert = root_key, root_cert
    _, leaf_cert = sign(tmp, "leaf-main", signer_key, signer_cert, 20, leaf_ext(dns, eku=leaf_eku, policy=leaf_policy), days=days)
    if revoke_leaf:
        crls.append(make_crl(tmp, "signer-revokes-leaf", signer_key, signer_cert, [leaf_cert]))
    if decoy_crl:
        decoy_key, decoy_root = root(tmp, "root-decoy")
        _, decoy_leaf = sign(tmp, "decoy-leaf", decoy_key, decoy_root, 20, leaf_ext("decoy.allowed.test"))
        crls.append(make_crl(tmp, "decoy-revokes-same-serial", decoy_key, decoy_root, [decoy_leaf]))
    evidence = tmp / "evidence"
    (evidence / "roots").mkdir(parents=True)
    (evidence / "intermediates").mkdir()
    (evidence / "crls").mkdir()
    shutil.copy(root_cert, evidence / "roots" / "root.pem")
    if extra_bad:
        bad_key, bad_root = root(tmp, "root-other")
        _, bad_int = sign(tmp, "a-decoy", bad_key, bad_root, 30, int_ext())
        shutil.copy(bad_int, evidence / "intermediates" / "a-decoy.pem")
    if with_intermediate:
        shutil.copy(int1_cert, evidence / "intermediates" / "z-main.pem")
        if deep_chain:
            shutil.copy(signer_cert, evidence / "intermediates" / "m-sub.pem")
    for idx, crl in enumerate(crls):
        shutil.copy(crl, evidence / "crls" / f"{idx}-{crl.name}")
    shutil.copy(leaf_cert, evidence / "leaf.pem")
    req = {
        "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + now_offset)),
        "dns_name": request_dns or dns,
        "extended_key_usage": request_eku,
        "required_policy": POLICY,
    }
    write(evidence / "request.json", json.dumps(req, sort_keys=True))
    return evidence


def attest(evidence):
    out = evidence.parent / "report.json"
    if out.exists():
        out.unlink()
    run([str(TOOL), str(evidence), str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["leaf_sha256", "reason", "verdict"]
    assert len(data["leaf_sha256"]) == 64
    int(data["leaf_sha256"], 16)
    assert data["leaf_sha256"] == leaf_fingerprint(evidence)
    again = out.read_text(encoding="utf-8")
    run([str(TOOL), str(evidence), str(out)])
    assert out.read_text(encoding="utf-8") == again
    return data


def leaf_fingerprint(evidence):
    der = subprocess.check_output(["openssl", "x509", "-in", str(evidence / "leaf.pem"), "-outform", "DER"])
    return hashlib.sha256(der).hexdigest()


def setup_module(module):
    """Install the current attestation command before behavioral checks."""
    run(["make", "-C", "/opt/x509audit", "install"])


def test_accepted_evidence_has_stable_attestation():
    """A valid local certificate path is accepted with a stable report shape and fingerprint."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), permitted=".allowed.test")
        report = attest(evidence)
        assert report["verdict"] == "trusted"
        assert report["reason"] == "ok"


def test_available_path_is_chosen_without_input_order_dependence():
    """Local evidence order does not prevent selecting a valid trust path."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), permitted=".allowed.test", extra_bad=True)
        report = attest(evidence)
        assert report["verdict"] == "trusted"
        assert report["reason"] == "ok"


def test_identity_constraints_affect_the_verdict():
    """SAN mismatches and CA name constraints are reported as name failures."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), dns="svc.present.test", request_dns="svc.requested.test")
        report = attest(evidence)
        assert report["verdict"] == "rejected"
        assert report["reason"] == "name"
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), dns="svc.other.test", permitted=".allowed.test")
        report = attest(evidence)
        assert report["verdict"] == "rejected"
        assert report["reason"] == "name"
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), dns="bad.blocked.test", excluded=".blocked.test")
        report = attest(evidence)
        assert report["verdict"] == "rejected"
        assert report["reason"] == "name"


def test_usage_and_policy_are_reported_with_priority():
    """Certificate authorization fields are distinguished in reports."""
    with tempfile.TemporaryDirectory() as d:
        usage = attest(make_case(pathlib.Path(d) / "u", leaf_eku="clientAuth", permitted=".allowed.test"))
        assert usage["verdict"] == "rejected"
        assert usage["reason"] == "usage"
    with tempfile.TemporaryDirectory() as d:
        policy = attest(make_case(pathlib.Path(d) / "p", leaf_policy=False, permitted=".allowed.test"))
        assert policy["verdict"] == "rejected"
        assert policy["reason"] == "policy"
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), leaf_policy=False, permitted=".allowed.test")
        req = json.loads((evidence / "request.json").read_text(encoding="utf-8"))
        req["required_policy"] = ""
        write(evidence / "request.json", json.dumps(req, sort_keys=True))
        no_policy_requested = attest(evidence)
        assert no_policy_requested["verdict"] == "trusted"
        assert no_policy_requested["reason"] == "ok"


def test_requested_client_auth_and_direct_roots_are_supported():
    """The requested EKU is authoritative and a root-signed leaf needs no intermediate."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), request_eku="clientAuth", leaf_eku="clientAuth", with_intermediate=False)
        report = attest(evidence)
        assert report["verdict"] == "trusted"
        assert report["reason"] == "ok"


def test_reason_priority_is_stable_when_multiple_checks_fail():
    """The public reason order is used when an artifact has several defects."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), days=30, now_offset=90 * 24 * 3600, revoke_leaf=True, dns="svc.other.test", leaf_eku="clientAuth", leaf_policy=False, permitted=".allowed.test")
        report = attest(evidence)
        assert report["verdict"] == "rejected"
        assert report["reason"] == "time"
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), revoke_leaf=True, dns="svc.other.test", leaf_eku="clientAuth", leaf_policy=False, permitted=".allowed.test")
        report = attest(evidence)
        assert report["verdict"] == "rejected"
        assert report["reason"] == "revocation"


def test_requested_audit_time_is_authoritative():
    """The report is based on the supplied audit instant."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), permitted=".allowed.test", days=30, now_offset=90 * 24 * 3600)
        report = attest(evidence)
        assert report["verdict"] == "rejected"
        assert report["reason"] == "time"


def test_crl_revocation_is_bound_to_the_issuer_and_path_member():
    """Issuer-matched CRLs revoke path members while unrelated CRLs with matching serials do not."""
    with tempfile.TemporaryDirectory() as d:
        valid = attest(make_case(pathlib.Path(d) / "valid", permitted=".allowed.test", decoy_crl=True))
        assert valid["verdict"] == "trusted"
        assert valid["reason"] == "ok"
    with tempfile.TemporaryDirectory() as d:
        leaf_revoked = attest(make_case(pathlib.Path(d) / "leaf", permitted=".allowed.test", revoke_leaf=True))
        assert leaf_revoked["verdict"] == "rejected"
        assert leaf_revoked["reason"] == "revocation"
    with tempfile.TemporaryDirectory() as d:
        int_revoked = attest(make_case(pathlib.Path(d) / "int", permitted=".allowed.test", revoke_intermediate=True))
        assert int_revoked["verdict"] == "rejected"
        assert int_revoked["reason"] == "revocation"


def test_unordered_deep_intermediate_chain_remains_valid():
    """A valid chain may require more than one unordered intermediate certificate."""
    with tempfile.TemporaryDirectory() as d:
        evidence = make_case(pathlib.Path(d), permitted=".allowed.test", deep_chain=True, extra_bad=True)
        report = attest(evidence)
        assert report["verdict"] == "trusted"
        assert report["reason"] == "ok"
