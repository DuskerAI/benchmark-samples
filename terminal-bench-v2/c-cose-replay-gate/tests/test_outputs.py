import hashlib
import hmac
import json
import subprocess
from pathlib import Path

APP = Path("/app/replaygate")


def head(mt, n):
    if n < 24:
        return bytes([(mt << 5) | n])
    if n < 256:
        return bytes([(mt << 5) | 24, n])
    if n < 65536:
        return bytes([(mt << 5) | 25]) + n.to_bytes(2, "big")
    if n < 2**32:
        return bytes([(mt << 5) | 26]) + n.to_bytes(4, "big")
    return bytes([(mt << 5) | 27]) + n.to_bytes(8, "big")


def cint(n):
    return head(0, n) if n >= 0 else head(1, -1 - n)


def bstr(b):
    return head(2, len(b)) + b


def tstr(s):
    b = s.encode()
    return head(3, len(b)) + b


def arr(xs):
    return head(4, len(xs)) + b"".join(xs)


def mp(items, sort=True):
    enc = [(k, v) for k, v in items]
    if sort:
        enc.sort(key=lambda kv: kv[0])
    return head(5, len(enc)) + b"".join(k + v for k, v in enc)


def tag18(x):
    return head(6, 18) + x


def sha_line(records):
    body = b"RG1\n" + b"".join(f"{e} {i}\n".encode() for e, i in records)
    return body + b"# " + hashlib.sha256(body).hexdigest().encode() + b"\n"


def keys_file(entries):
    rows = [
        mp([
            (tstr("kid"), bstr(e["kid"])),
            (tstr("not_after"), cint(e["not_after"])),
            (tstr("not_before"), cint(e["not_before"])),
            (tstr("ops"), arr([tstr(x) for x in e["ops"]])),
            (tstr("secret"), bstr(e["secret"])),
        ])
        for e in entries
    ]
    return arr(rows)


def protected(kid, epoch, op, crit=(100, 101), sort=True, front_kid=False):
    pairs = [
        (cint(1), cint(-16)),
        (cint(2), arr([cint(x) for x in crit])),
        (cint(4), bstr(kid)),
        (cint(100), cint(epoch)),
        (cint(101), tstr(op)),
    ]
    if front_kid:
        pairs = [pairs[2], pairs[0], pairs[1], pairs[3], pairs[4]]
        sort = False
    return mp(pairs, sort=sort)


def payload(epoch, uid, seq=1, component="boot", body=b"image"):
    return mp([
        (cint(1), cint(epoch)),
        (cint(2), bstr(uid)),
        (cint(3), tstr(component)),
        (cint(4), cint(seq)),
        (cint(5), bstr(body)),
    ])


def mac(secret, data):
    return hmac.new(secret, data, hashlib.sha256).digest()


def sign1(secret, prot, detached):
    sig_input = arr([tstr("Signature1"), bstr(prot), bstr(b""), bstr(detached)])
    sig = mac(secret, sig_input)
    return tag18(arr([bstr(prot), mp([]), b"\xf6", bstr(sig)])), sig


def counter(secret, cprot, pprot, psig, detached):
    sig_input = arr([tstr("CounterSignature"), bstr(cprot), bstr(pprot), bstr(psig), bstr(detached)])
    sig = mac(secret, sig_input)
    return tag18(arr([bstr(cprot), mp([]), b"\xf6", bstr(sig)]))


def bundle(primary_secret, counter_secret, epoch=7, uid=b"upd", pcrit=(100, 101), ccrit=(100, 101),
           primary_op="install", counter_op="admit", primary_kid=b"sign-A", counter_kid=b"ctr-A",
           noncanonical_primary=False):
    pl = payload(epoch, uid, seq=epoch + 11, body=b"payload-" + uid)
    pp = protected(primary_kid, epoch, primary_op, pcrit, front_kid=noncanonical_primary)
    cp = protected(counter_kid, epoch, counter_op, ccrit)
    ps, psig = sign1(primary_secret, pp, pl)
    cs = counter(counter_secret, cp, pp, psig, pl)
    return mp([(tstr("counter"), cs), (tstr("payload"), bstr(pl)), (tstr("sign1"), ps)])


def build():
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True)


def run_gate(tmp_path, key_bytes, bundle_bytes, ledger=None):
    k = tmp_path / "keys.cbor"
    b = tmp_path / "bundle.cbor"
    ledger_path = tmp_path / "ledger"
    o = tmp_path / "report.json"
    k.write_bytes(key_bytes)
    b.write_bytes(bundle_bytes)
    if ledger is not None:
        ledger_path.write_bytes(ledger)
    p = subprocess.run(
        [
            str(APP / "bin/replaygate"),
            "--keys",
            str(k),
            "--bundle",
            str(b),
            "--ledger",
            str(ledger_path),
            "--out",
            str(o),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert o.exists(), p.stderr
    return p.returncode, json.loads(o.read_text()), ledger_path


def base_keys(counter_ops=("admit",), primary_ops=("install",)):
    return keys_file([
        {"kid": b"sign-A", "secret": b"s" * 32, "not_before": 1, "not_after": 20, "ops": list(primary_ops)},
        {"kid": b"ctr-A", "secret": b"c" * 32, "not_before": 1, "not_after": 20, "ops": list(counter_ops)},
        {"kid": b"sign-A", "secret": b"x" * 32, "not_before": 21, "not_after": 40, "ops": ["install"]},
    ])


def test_acceptance_persists_admission_state(tmp_path):
    """A valid offline admission is accepted once and rejected after the ledger is reopened."""
    build()
    kb = base_keys()
    bb = bundle(b"s" * 32, b"c" * 32, epoch=7, uid=b"alpha")
    rc, report, ledger = run_gate(tmp_path, kb, bb)
    assert rc == 0
    assert report["status"] == "ACCEPT"
    _, report, _ = run_gate(tmp_path, kb, bb, ledger=ledger.read_bytes())
    assert report["status"] == "REJECT"
    assert report["reason"] == "replay"


def test_canonical_protected_bytes_are_enforced(tmp_path):
    """Admission rejects signed data whose protected header bytes are not canonical CBOR."""
    build()
    kb = base_keys()
    bb = bundle(b"s" * 32, b"c" * 32, epoch=8, uid=b"canon001", noncanonical_primary=True)
    _, report, _ = run_gate(tmp_path, kb, bb)
    assert report["status"] == "REJECT"
    assert report["reason"] in {"malformed", "policy"}


def test_critical_header_completeness_is_security_policy(tmp_path):
    """Protected critical headers must list every private critical label and reject as malformed or policy."""
    build()
    bb = bundle(b"s" * 32, b"c" * 32, epoch=9, uid=b"beta", ccrit=(100,))
    _, report, _ = run_gate(tmp_path, base_keys(), bb)
    assert report["status"] == "REJECT"
    assert report["reason"] in {"malformed", "policy"}


def test_key_operations_are_security_policy(tmp_path):
    """Signing keys are only valid for operations listed in their ops array."""
    build()
    bb = bundle(b"s" * 32, b"c" * 32, epoch=10, uid=b"gamma")
    _, report, _ = run_gate(tmp_path, base_keys(counter_ops=("install",)), bb)
    assert report["status"] == "REJECT"


def test_documented_policy_rejections_and_report_schema(tmp_path):
    """Epoch, signer, and key-window policy failures use the documented report schema."""
    build()
    kb = base_keys()
    pl = payload(13, b"epoch-mismatch")
    pp = protected(b"sign-A", 14, "install")
    cp = protected(b"ctr-A", 14, "admit")
    ps, psig = sign1(b"s" * 32, pp, pl)
    cs = counter(b"c" * 32, cp, pp, psig, pl)
    mismatched_epoch = mp([(tstr("counter"), cs), (tstr("payload"), bstr(pl)), (tstr("sign1"), ps)])
    _, report, _ = run_gate(tmp_path, kb, mismatched_epoch)
    assert set(report) == {"status", "reason", "epoch", "id"}
    assert report["status"] == "REJECT"
    assert report["reason"] == "policy"

    same_signer = bundle(b"s" * 32, b"s" * 32, epoch=14, uid=b"same-kid", counter_kid=b"sign-A")
    _, report, _ = run_gate(tmp_path, kb, same_signer)
    assert set(report) == {"status", "reason", "epoch", "id"}
    assert report["status"] == "REJECT"
    assert report["reason"] == "policy"

    expired_key = bundle(b"s" * 32, b"c" * 32, epoch=25, uid=b"expired")
    _, report, _ = run_gate(tmp_path, kb, expired_key)
    assert set(report) == {"status", "reason", "epoch", "id"}
    assert report["status"] == "REJECT"
    assert report["reason"] == "bad-signature"


def test_corrupt_base_ledger_rejects_with_ledger_reason(tmp_path):
    """A corrupt existing ledger rejects the admission with the documented ledger reason."""
    build()
    kb = base_keys()
    bb = bundle(b"s" * 32, b"c" * 32, epoch=18, uid=b"bad-ledger")
    _, report, _ = run_gate(tmp_path, kb, bb, ledger=b"RG1\n18 626164\n# not-a-valid-checksum\n")
    assert set(report) == {"status", "reason", "epoch", "id"}
    assert report["status"] == "REJECT"
    assert report["reason"] == "ledger"


def test_complete_interrupted_ledger_is_finished_before_admission(tmp_path):
    """A complete temporary ledger update is honored after restart before replay checks."""
    build()
    kb = base_keys()
    first = bundle(b"s" * 32, b"c" * 32, epoch=11, uid=b"old")
    _, report, ledger = run_gate(tmp_path, kb, first)
    assert report["status"] == "ACCEPT"
    new_id = b"pending"
    (tmp_path / "ledger.tmp").write_bytes(sha_line([(11, "6f6c64"), (12, new_id.hex())]))
    pending = bundle(b"s" * 32, b"c" * 32, epoch=12, uid=new_id)
    (tmp_path / "keys2.cbor").write_bytes(kb)
    (tmp_path / "bundle2.cbor").write_bytes(pending)
    out = tmp_path / "report2.json"
    p = subprocess.run([str(APP / "bin/replaygate"), "--keys", str(tmp_path / "keys2.cbor"), "--bundle", str(tmp_path / "bundle2.cbor"), "--ledger", str(ledger), "--out", str(out)], text=True, capture_output=True, check=False)
    assert out.exists(), p.stderr
    report = json.loads(out.read_text())
    assert report["status"] == "REJECT"
    assert report["reason"] == "replay"


def test_corrupt_temporary_ledger_is_ignored_before_admission(tmp_path):
    """A corrupt temporary ledger is removed before a new admission is evaluated."""
    build()
    kb = base_keys()
    first = bundle(b"s" * 32, b"c" * 32, epoch=15, uid=b"base")
    _, report, ledger = run_gate(tmp_path, kb, first)
    assert report["status"] == "ACCEPT"

    bad_tmp = tmp_path / "ledger.tmp"
    bad_tmp.write_bytes(b"RG1\n16 636f7272757074\n# not-a-valid-checksum\n")
    corrupt_id = b"after-corrupt-tmp"
    rc, report, ledger = run_gate(tmp_path, kb, bundle(b"s" * 32, b"c" * 32, epoch=16, uid=corrupt_id), ledger=ledger.read_bytes())
    assert rc == 0
    assert report["status"] == "ACCEPT"
    assert not bad_tmp.exists()
    assert f"16 {corrupt_id.hex()}\n".encode() in ledger.read_bytes()


def test_non_extending_temporary_ledger_is_ignored_before_admission(tmp_path):
    """A valid but non-extending temporary ledger is removed before a new admission is evaluated."""
    build()
    kb = base_keys()
    first = bundle(b"s" * 32, b"c" * 32, epoch=15, uid=b"base")
    _, report, ledger = run_gate(tmp_path, kb, first)
    assert report["status"] == "ACCEPT"

    bad_tmp = tmp_path / "ledger.tmp"
    bad_tmp.write_bytes(sha_line([(99, "6e6f742d616e2d657874656e73696f6e")]))
    non_extending_id = b"after-non-extending-tmp"
    rc, report, ledger = run_gate(tmp_path, kb, bundle(b"s" * 32, b"c" * 32, epoch=17, uid=non_extending_id), ledger=ledger.read_bytes())
    assert rc == 0
    assert report["status"] == "ACCEPT"
    assert not bad_tmp.exists()
    assert f"17 {non_extending_id.hex()}\n".encode() in ledger.read_bytes()
