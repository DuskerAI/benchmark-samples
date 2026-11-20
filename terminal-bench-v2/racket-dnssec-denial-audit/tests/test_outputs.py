import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

AUDIT = ["racket", "/opt/dnssec-audit/audit.rkt"]
NOW = 1_710_000_000
ZONE = "example.test."


def canon_name(name):
    text = str(name).strip().lower()
    return text if text.endswith(".") else text + "."


def wire(name):
    parts = [p.encode() for p in canon_name(name).split(".") if p]
    return b"".join(bytes([len(p)]) + p for p in parts) + b"\x00"


def canon_value(value):
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={canon_value(value[k])}" for k in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(sorted(canon_value(v) for v in value)) + "]"
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def canonical_rrset(rrset):
    records = ";".join(sorted(canon_value(r) for r in rrset["records"]))
    return "|".join([canon_name(rrset["owner"]), rrset["type"].upper(), str(rrset["ttl"]), records])


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


KSK = {"key_tag": 9031, "algorithm": 8, "role": "KSK", "public": "ksk-alpha"}
ZSK = {"key_tag": 9042, "algorithm": 8, "role": "ZSK", "public": "zsk-alpha"}


def dnskey_digest(key):
    return digest("|".join([str(key["key_tag"]), str(key["algorithm"]), key["role"].upper(), key["public"]]))


def sign(rrset, key, inception=None, expiration=None, signer=ZONE):
    inception = NOW - 500 if inception is None else inception
    expiration = NOW + 500 if expiration is None else expiration
    sig = {
        "type_covered": rrset["type"].upper(),
        "algorithm": key["algorithm"],
        "key_tag": key["key_tag"],
        "signer": signer,
        "inception": inception,
        "expiration": expiration,
    }
    body = "|".join([
        key["public"],
        canonical_rrset(rrset),
        sig["type_covered"],
        canon_name(sig["signer"]),
        str(sig["inception"]),
        str(sig["expiration"]),
    ])
    sig["signature"] = digest(body)
    rrset["rrsig"] = sig
    return rrset


def base_proof(question_name="missing.service.example.test", qtype="A"):
    dnskey_rrset = {
        "owner": ZONE,
        "type": "DNSKEY",
        "ttl": 3600,
        "records": [ZSK.copy(), KSK.copy()],
    }
    return {
        "now": NOW,
        "zone": ZONE,
        "question": {"name": question_name, "type": qtype},
        "trust_anchor": {
            "key_tag": KSK["key_tag"],
            "algorithm": KSK["algorithm"],
            "digest_type": 2,
            "digest": dnskey_digest(KSK),
        },
        "dnskeys": [KSK.copy(), ZSK.copy()],
        "rrsets": [sign(dnskey_rrset, KSK)],
    }


def nsec(owner, next_name, types=None):
    rrset = {
        "owner": owner,
        "type": "NSEC",
        "ttl": 600,
        "records": [{"next": next_name, "types": types or ["RRSIG", "NSEC"]}],
    }
    return sign(rrset, ZSK)


def nsec3_hash(name, salt, iterations):
    h = digest(f"{canon_name(name)}|{salt}")
    for _ in range(iterations):
        h = digest(f"{h}|{salt}")
    return h


def interval_around(h):
    value = int(h, 16)
    low = f"{(value - 1) % (1 << 256):064x}"
    high = f"{(value + 1) % (1 << 256):064x}"
    return low, high


def wrap_interval_around(h):
    value = int(h, 16)
    owner = f"{(value - 1) % (1 << 256):064x}"
    next_hash = "0" * 64
    assert owner > next_hash and owner < h
    return owner, next_hash


def hash_between(owner, next_hash, h):
    owner = owner.lower()
    next_hash = next_hash.lower()
    h = h.lower()
    if owner < next_hash:
        return owner < h < next_hash
    return owner < h or h < next_hash


def nsec3(owner_hash, next_hash, salt, iterations, opt_out=False, types=None):
    rrset = {
        "owner": owner_hash,
        "type": "NSEC3",
        "ttl": 600,
        "records": [{
            "next_hash": next_hash,
            "salt": salt,
            "iterations": iterations,
            "opt_out": opt_out,
            "types": types or ["RRSIG", "NSEC3"],
        }],
    }
    return sign(rrset, ZSK)


def nsec3_exact(name, salt, iterations, types=None):
    h = nsec3_hash(name, salt, iterations)
    next_hash = f"{(int(h, 16) + 1) % (1 << 256):064x}"
    return nsec3(h, next_hash, salt, iterations, types=types or ["NS", "RRSIG", "NSEC3"])


def nsec3_covering_name(name, salt, iterations, opt_out=False, wrap=False):
    h = nsec3_hash(name, salt, iterations)
    owner, next_hash = wrap_interval_around(h) if wrap else interval_around(h)
    assert hash_between(owner, next_hash, h)
    return nsec3(owner, next_hash, salt, iterations, opt_out=opt_out)


def nsec3_rrsets_cover_hash(rrsets, h):
    return any(
        rrset["type"].upper() == "NSEC3"
        and hash_between(rrset["owner"], rrset["records"][0]["next_hash"], h)
        for rrset in rrsets
    )


def run_audit(proof):
    with tempfile.TemporaryDirectory() as td:
        proof_path = Path(td) / "proof.json"
        out_path = Path(td) / "report.json"
        proof_path.write_text(json.dumps(proof), encoding="utf-8")
        result = subprocess.run(
            AUDIT + ["--proof", str(proof_path), "--out", str(out_path)],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(out_path.read_text(encoding="utf-8"))


def assert_admitted(proof, expected):
    report = run_audit(proof)
    assert set(report) == {"admitted", "reason"}
    assert report["admitted"] is expected
    assert report["reason"] == ("secure-denial" if expected else "bogus")


def test_admission_case_a():
    """NSEC proof admits an absent name when wildcard denial is proven."""
    proof = base_proof("MiSsInG.Service.Example.Test", "A")
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test.", ["NSEC", "RRSIG"]),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert wire("alpha.service.example.test.") < wire("missing.service.example.test.") < wire("zzzzzzzz.service.example.test.")
    assert_admitted(proof, True)


def test_admission_case_b():
    """NSEC proof rejects denial that omits wildcard absence."""
    proof = base_proof("missing.service.example.test", "A")
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test."),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_c():
    """Signed denial is rejected when RRSIG validity starts in the future."""
    proof = base_proof("absent.service.example.test", "A")
    proof["rrsets"].extend([
        sign({"owner": "aaa.service.example.test.", "type": "NSEC", "ttl": 600, "records": [{"next": "zzzzzzz.service.example.test.", "types": ["NSEC", "RRSIG"]}]}, ZSK, inception=NOW + 100, expiration=NOW + 1000),
        sign({"owner": "service.example.test.", "type": "NSEC", "ttl": 600, "records": [{"next": "zzzzzzzz.service.example.test.", "types": ["NS", "NSEC", "RRSIG"]}]}, ZSK, inception=NOW + 100, expiration=NOW + 1000),
        sign({"owner": "*.service.example.test.", "type": "NSEC", "ttl": 600, "records": [{"next": "aaa.service.example.test.", "types": ["NSEC", "RRSIG"]}]}, ZSK, inception=NOW + 100, expiration=NOW + 1000),
    ])
    assert_admitted(proof, False)


def test_admission_case_d():
    """Trust anchor is rejected unless it uses the accepted digest."""
    proof = base_proof("missing.service.example.test", "A")
    proof["trust_anchor"]["digest_type"] = 1
    proof["trust_anchor"]["digest"] = digest("different-anchor")
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test."),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "NSEC", "RRSIG"]),
        nsec("*.service.example.test.", "alpha.service.example.test."),
    ])
    assert_admitted(proof, False)


def test_admission_case_e():
    """NSEC3 NXDOMAIN admits when CE, next-closer, and wildcard hashes are proven."""
    qname = "ghost.api.deep.example.test"
    closest_encloser = "deep.example.test."
    next_closer = "api.deep.example.test."
    wildcard = "*.deep.example.test."
    salt = "cab5"
    iterations = 2
    proof = base_proof(qname, "A")
    proof["rrsets"].extend([
        nsec3_exact(closest_encloser, salt, iterations, ["NS", "RRSIG", "NSEC3"]),
        nsec3_covering_name(next_closer, salt, iterations),
        nsec3_covering_name(wildcard, salt, iterations),
    ])
    assert not nsec3_rrsets_cover_hash(proof["rrsets"], nsec3_hash(qname, salt, iterations))
    assert_admitted(proof, True)


def test_admission_case_f():
    """NSEC3 opt-out is rejected when it is part of a full denial proof."""
    qname = "unsigned.api.deep.example.test"
    closest_encloser = "deep.example.test."
    next_closer = "api.deep.example.test."
    wildcard = "*.deep.example.test."
    salt = "cab5"
    iterations = 3
    proof = base_proof(qname, "A")
    proof["rrsets"].extend([
        nsec3_exact(closest_encloser, salt, iterations, ["NS", "RRSIG", "NSEC3"]),
        nsec3_covering_name(next_closer, salt, iterations, opt_out=True),
        nsec3_covering_name(wildcard, salt, iterations),
    ])
    assert_admitted(proof, False)


def test_admission_case_f_strong_opt_out_covering_query_hash():
    """Covering only the queried-name hash is not an NSEC3 NXDOMAIN proof."""
    qname = "covered-only.api.deep.example.test"
    salt = "d00d"
    iterations = 1
    owner, next_hash = interval_around(nsec3_hash(qname, salt, iterations))
    proof = base_proof(qname, "AAAA")
    proof["rrsets"].append(nsec3(owner, next_hash, salt, iterations, opt_out=False))
    assert_admitted(proof, False)


def test_admission_case_g():
    """Exact wildcard NSEC admits NODATA when the requested type is absent."""
    proof = base_proof("*.service.example.test", "TXT")
    proof["rrsets"].append(
        nsec(
            "*.service.example.test.",
            "omega.service.example.test.",
            ["RRSIG", "NSEC", "A"],
        )
    )
    assert_admitted(proof, True)


def test_admission_case_h():
    """NSEC3 denial admits when a required component uses a wrap-around interval."""
    qname = "wrap.leaf.deep.example.test"
    closest_encloser = "deep.example.test."
    next_closer = "leaf.deep.example.test."
    wildcard = "*.deep.example.test."
    salt = "bada55"
    iterations = 4
    proof = base_proof(qname, "A")
    proof["rrsets"].extend([
        nsec3_exact(closest_encloser, salt, iterations, ["NS", "RRSIG", "NSEC3"]),
        nsec3_covering_name(next_closer, salt, iterations),
        nsec3_covering_name(wildcard, salt, iterations, wrap=True),
    ])
    assert_admitted(proof, True)


def test_admission_case_i():
    """Trust anchor digest must bind to the KSK, not just its key tag and algorithm."""
    proof = base_proof("missing.service.example.test", "A")
    proof["trust_anchor"]["digest"] = digest("wrong-ksk-digest")
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test.", ["NSEC", "RRSIG"]),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_j():
    """DNSKEY RRsets are rejected when signed with the ZSK role."""
    dnskey_rrset = {
        "owner": ZONE,
        "type": "DNSKEY",
        "ttl": 3600,
        "records": [ZSK.copy(), KSK.copy()],
    }
    proof = base_proof("missing.service.example.test", "A")
    proof["rrsets"][0] = sign(dnskey_rrset, ZSK)
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test.", ["NSEC", "RRSIG"]),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_k():
    """NSEC RRsets are rejected when signed with the KSK role."""
    proof = base_proof("missing.service.example.test", "A")
    proof["rrsets"].extend([
        sign({
            "owner": "alpha.service.example.test.",
            "type": "NSEC",
            "ttl": 600,
            "records": [{"next": "zzzzzzzz.service.example.test.", "types": ["NSEC", "RRSIG"]}],
        }, KSK),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_l():
    """Exact NSEC at a CNAME owner is not a denial of the requested address type."""
    proof = base_proof("alias.service.example.test", "A")
    proof["rrsets"].append(
        nsec(
            "alias.service.example.test.",
            "omega.service.example.test.",
            ["CNAME", "NSEC", "RRSIG"],
        )
    )
    assert_admitted(proof, False)


def test_admission_case_m():
    """NSEC coverage uses DNS wire-order, not ordinary string order."""
    qname = "zzz.example.test"
    proof = base_proof(qname, "A")
    proof["rrsets"].extend([
        nsec("b.example.test.", "aa.example.test.", ["NSEC", "RRSIG"]),
        nsec("example.test.", "b.example.test.", ["SOA", "NS", "NSEC", "RRSIG"]),
        nsec("*.example.test.", "b.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert wire("b.example.test.") < wire("aa.example.test.")
    assert not (wire("b.example.test.") < wire(qname) < wire("aa.example.test."))
    assert "b.example.test." < canon_name(qname)
    assert_admitted(proof, False)


def test_admission_case_n():
    """A future-valid DNSKEY RRSIG makes the entire proof bogus."""
    dnskey_rrset = {
        "owner": ZONE,
        "type": "DNSKEY",
        "ttl": 3600,
        "records": [ZSK.copy(), KSK.copy()],
    }
    proof = base_proof("missing.service.example.test", "A")
    proof["rrsets"][0] = sign(dnskey_rrset, KSK, inception=NOW + 20, expiration=NOW + 2000)
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test.", ["NSEC", "RRSIG"]),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_o():
    """NSEC3 proof components must use one consistent salt and iteration count."""
    qname = "mixed.api.deep.example.test"
    closest_encloser = "deep.example.test."
    next_closer = "api.deep.example.test."
    wildcard = "*.deep.example.test."
    salt = "aa11"
    other_salt = "bb22"
    iterations = 2
    proof = base_proof(qname, "A")
    proof["rrsets"].extend([
        nsec3_exact(closest_encloser, salt, iterations, ["NS", "RRSIG", "NSEC3"]),
        nsec3_covering_name(next_closer, salt, iterations),
        nsec3_covering_name(wildcard, other_salt, iterations),
        nsec3_covering_name(qname, salt, iterations),
    ])
    assert_admitted(proof, False)


def test_admission_case_p():
    """NSEC wildcard denial must use the closest existing ancestor, not just the parent."""
    proof = base_proof("absent.service.example.test", "A")
    proof["rrsets"].extend([
        nsec("aaaaa.service.example.test.", "zzzzzzz.service.example.test.", ["NSEC", "RRSIG"]),
        nsec("example.test.", "zzzzzzzz.example.test.", ["SOA", "NS", "NSEC", "RRSIG"]),
        nsec("*.service.example.test.", "aaaaa.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_q():
    """NSEC3 exact NODATA admits when the requested type and CNAME are absent."""
    qname = "nodata.api.deep.example.test"
    salt = "cefa"
    iterations = 2
    proof = base_proof(qname, "AAAA")
    proof["rrsets"].append(
        nsec3_exact(qname, salt, iterations, ["A", "RRSIG", "NSEC3"])
    )
    assert_admitted(proof, True)


def test_admission_case_r():
    """NSEC3 exact NODATA is bogus when the exact owner has a CNAME."""
    qname = "alias-hash.api.deep.example.test"
    salt = "cefb"
    iterations = 2
    proof = base_proof(qname, "A")
    proof["rrsets"].append(
        nsec3_exact(qname, salt, iterations, ["CNAME", "RRSIG", "NSEC3"])
    )
    assert_admitted(proof, False)


def test_admission_case_s():
    """An expired NSEC RRSIG makes an otherwise complete denial proof bogus."""
    proof = base_proof("expired.service.example.test", "A")
    proof["rrsets"].extend([
        sign({
            "owner": "aaaaa.service.example.test.",
            "type": "NSEC",
            "ttl": 600,
            "records": [{"next": "zzzzzzz.service.example.test.", "types": ["NSEC", "RRSIG"]}],
        }, ZSK, inception=NOW - 2000, expiration=NOW - 1),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "NSEC", "RRSIG"]),
        nsec("*.service.example.test.", "aaaaa.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_t():
    """A wildcard NSEC owner below a name does not independently prove that name exists."""
    proof = base_proof("absent.branch.example.test", "A")
    proof["rrsets"].extend([
        nsec("aaaaa.branch.example.test.", "zzzzzzz.branch.example.test.", ["NSEC", "RRSIG"]),
        nsec("example.test.", "zzzzzzzz.example.test.", ["SOA", "NS", "NSEC", "RRSIG"]),
        nsec("*.branch.example.test.", "*.cranch.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_u():
    """RRSIG signer names outside the proof zone make denial evidence bogus."""
    proof = base_proof("missing.service.example.test", "A")
    proof["rrsets"].extend([
        sign({
            "owner": "alpha.service.example.test.",
            "type": "NSEC",
            "ttl": 600,
            "records": [{"next": "zzzzzzzz.service.example.test.", "types": ["NSEC", "RRSIG"]}],
        }, ZSK, signer="elsewhere.test."),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_v():
    """An exact NSEC owner with CNAME cannot be overridden by an NXDOMAIN chain."""
    proof = base_proof("alias.service.example.test", "A")
    proof["rrsets"].extend([
        nsec("alias.service.example.test.", "alias2.service.example.test.", ["CNAME", "NSEC", "RRSIG"]),
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test.", ["NSEC", "RRSIG"]),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["NS", "RRSIG", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["NSEC", "RRSIG"]),
    ])
    assert_admitted(proof, False)


def test_admission_case_w():
    """An exact NSEC3 owner with CNAME cannot be overridden by an NXDOMAIN chain."""
    qname = "alias.api.deep.example.test"
    closest_encloser = "deep.example.test."
    next_closer = "api.deep.example.test."
    wildcard = "*.deep.example.test."
    salt = "c0de"
    iterations = 3
    proof = base_proof(qname, "A")
    proof["rrsets"].extend([
        nsec3_exact(qname, salt, iterations, ["CNAME", "RRSIG", "NSEC3"]),
        nsec3_exact(closest_encloser, salt, iterations, ["NS", "RRSIG", "NSEC3"]),
        nsec3_covering_name(next_closer, salt, iterations),
        nsec3_covering_name(wildcard, salt, iterations),
    ])
    assert_admitted(proof, False)


def test_admission_case_x():
    """Canonical signatures are independent of JSON record and list ordering."""
    proof = base_proof("shuffle.service.example.test", "A")
    proof["rrsets"].extend([
        nsec("alpha.service.example.test.", "zzzzzzzz.service.example.test.", ["RRSIG", "NSEC"]),
        nsec("service.example.test.", "zzzzzzzz.service.example.test.", ["RRSIG", "NS", "NSEC"]),
        nsec("*.service.example.test.", "alpha.service.example.test.", ["RRSIG", "NSEC"]),
    ])
    assert_admitted(proof, True)
