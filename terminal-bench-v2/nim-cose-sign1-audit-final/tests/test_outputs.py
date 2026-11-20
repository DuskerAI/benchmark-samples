# ruff: noqa
import hashlib
import hmac
import json
import shutil
import subprocess
from pathlib import Path


APP = Path("/app/coseaudit")
BIN = APP / "bin" / "coseaudit"
NOW = 1_700_000_000
KEYS = {
    "alpha": bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
    "beta": bytes.fromhex("1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100"),
}
ALT_KEYS = {
    "gamma": bytes.fromhex("202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f"),
    "delta": bytes.fromhex("3f3e3d3c3b3a393837363534333231302f2e2d2c2b2a29282726252423222120"),
}
STATUSES = ["bad-claims", "bad-signature", "expired", "malformed", "not-yet-valid", "unsupported", "valid"]


def enc_len(major, n):
    if n < 24:
        return bytes([(major << 5) | n])
    if n <= 0xFF:
        return bytes([(major << 5) | 24, n])
    if n <= 0xFFFF:
        return bytes([(major << 5) | 25]) + n.to_bytes(2, "big")
    if n <= 0xFFFFFFFF:
        return bytes([(major << 5) | 26]) + n.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + n.to_bytes(8, "big")


def cbor(value):
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        return enc_len(0, value) if value >= 0 else enc_len(1, -1 - value)
    if isinstance(value, bytes):
        return enc_len(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return enc_len(3, len(raw)) + raw
    if isinstance(value, list):
        return enc_len(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, tuple) and value and value[0] == "map":
        pairs = [(cbor(k), cbor(v)) for k, v in value[1]]
        pairs.sort(key=lambda p: p[0])
        return enc_len(5, len(pairs)) + b"".join(k + v for k, v in pairs)
    raise TypeError(value)


def raw_map(pairs):
    return enc_len(5, len(pairs)) + b"".join(cbor(k) + cbor(v) for k, v in pairs)


def indef_map(pairs):
    return b"\xbf" + b"".join(cbor(k) + cbor(v) for k, v in pairs) + b"\xff"


def split_bstr(data, at):
    return b"\x5f" + cbor(data[:at]) + cbor(data[at:]) + b"\xff"


def make_token(kid="alpha", claims=None, protected_pairs=None, unprotected_pairs=None, payload_bytes=None, signature_key=None, tamper=False, payload_indef=False, protected_indef=False, signature_indef=False, tag=True, keyring=KEYS):
    if claims is None:
        claims = {"iss": "issuer-a", "sub": "subject-a", "aud": "terminal-bench", "iat": NOW - 20, "exp": NOW + 900, "scope": ["write", "read"]}
    if protected_pairs is None:
        protected = cbor(("map", [(1, -16), (4, kid.encode())]))
    elif isinstance(protected_pairs, bytes):
        protected = protected_pairs
    else:
        protected = raw_map(protected_pairs)
    if unprotected_pairs is None:
        unprotected = cbor(("map", []))
    elif isinstance(unprotected_pairs, bytes):
        unprotected = unprotected_pairs
    else:
        unprotected = cbor(("map", unprotected_pairs))
    if payload_bytes is None:
        payload_bytes = cbor(("map", list(claims.items())))
    key_name = signature_key or kid
    sig_structure = cbor(["Signature1", protected, b"", payload_bytes])
    sig = hmac.new(keyring[key_name], sig_structure, hashlib.sha256).digest()
    if tamper:
        sig = sig[:-1] + bytes([sig[-1] ^ 0x55])
    protected_field = split_bstr(protected, 1) if protected_indef else cbor(protected)
    payload_field = split_bstr(payload_bytes, 5) if payload_indef else cbor(payload_bytes)
    signature_field = split_bstr(sig, 16) if signature_indef else cbor(sig)
    body = enc_len(4, 4) + protected_field + unprotected + payload_field + signature_field
    return ((b"\xd2" if tag else b"") + body).hex()


def raw_cose(protected_field, unprotected_field, payload_field, signature_field, tag=True):
    body = enc_len(4, 4) + protected_field + unprotected_field + payload_field + signature_field
    return ((b"\xd2" if tag else b"") + body).hex()


def corpus_primary():
    duplicate_payload = raw_map([("iss", "issuer-d"), ("sub", "subject-d"), ("aud", "wrong"), ("aud", "terminal-bench"), ("iat", NOW - 10), ("exp", NOW + 800), ("scope", ["read"])])
    unsupported = [(1, -16), (2, [99]), (4, b"alpha"), (99, True)]
    return [
        {"id": "zeta-valid", "cose": make_token(payload_indef=True)},
        {"id": "alpha-expired", "cose": make_token(claims={"iss": "issuer-b", "sub": "subject-b", "aud": "terminal-bench", "iat": NOW - 100, "exp": NOW - 1, "scope": ["read"]})},
        {"id": "gamma-unsupported", "cose": make_token(protected_pairs=unsupported)},
        {"id": "beta-badsig", "cose": make_token(kid="beta", claims={"iss": "issuer-c", "sub": "subject-c", "aud": "terminal-bench", "iat": NOW - 10, "exp": NOW + 200, "scope": ["read"]}, tamper=True)},
        {"id": "delta-malformed", "cose": make_token(payload_bytes=duplicate_payload)},
        {"id": "epsilon-claims", "cose": make_token(claims={"iss": "issuer-e", "sub": "", "aud": "terminal-bench", "iat": NOW - 10, "exp": NOW + 200, "scope": ["read"]})},
        {"id": "eta-future", "cose": make_token(claims={"iss": "issuer-f", "sub": "subject-f", "aud": "terminal-bench", "iat": NOW - 10, "nbf": NOW + 5, "exp": NOW + 200, "scope": ["read"]})},
        {"id": "theta-skewed", "cose": make_token(claims={"iss": "issuer-i", "sub": "subject-i", "aud": "terminal-bench", "iat": NOW + 61, "exp": NOW + 200, "scope": ["read"]})},
        {"id": "iota-precedence", "cose": make_token(claims={"iss": "issuer-j", "sub": "subject-j", "aud": "terminal-bench", "iat": NOW - 10, "nbf": NOW + 5, "exp": NOW - 1, "scope": ["read"]})},
    ]


def corpus_secondary():
    duplicate_protected = raw_map([(1, -16), (4, b"ghost"), (4, b"alpha")])
    return [
        {"id": "n-missing-iat", "cose": make_token(claims={"iss": "issuer-k", "sub": "subject-k", "aud": "terminal-bench", "exp": NOW + 77, "scope": ["read"]})},
        {"id": "m-middle", "cose": make_token(kid="beta", claims={"iss": "issuer-g", "sub": "subject-g", "aud": "terminal-bench", "iat": NOW - 4, "exp": NOW + 77, "scope": ["audit", "read", "audit"]})},
        {"id": "a-front", "cose": make_token(protected_pairs=duplicate_protected)},
        {"id": "x-back", "cose": make_token(claims={"iss": "issuer-h", "sub": "subject-h", "aud": "elsewhere", "iat": NOW - 4, "exp": NOW + 77, "scope": ["read"]})},
    ]


def corpus_hardened():
    unknown_noncritical = [(1, -16), (4, b"alpha"), (42, True)]
    duplicate_crit = [(1, -16), (2, [1, 1]), (4, b"alpha")]
    indefinite_protected = indef_map([(1, -16), (4, b"alpha")])
    indefinite_payload = indef_map([("iss", "issuer-m"), ("sub", "subject-m"), ("aud", "terminal-bench"), ("iat", NOW - 4), ("exp", NOW + 77), ("scope", ["read"])])
    return [
        {"id": "a0-badhex", "cose": "abc"},
        {"id": "b1-untagged-valid", "cose": make_token(tag=False, claims={"iss": "issuer-u", "sub": "subject-u", "aud": "terminal-bench", "iat": NOW - 4, "exp": NOW + 77, "scope": ["deploy", "read"]})},
        {"id": "c2-unprotected", "cose": make_token(unprotected_pairs=[(4, b"alpha")])},
        {"id": "d3-extra-protected", "cose": make_token(protected_pairs=unknown_noncritical)},
        {"id": "e4-duplicate-crit", "cose": make_token(protected_pairs=duplicate_crit)},
        {"id": "f5-scope-mixed", "cose": make_token(claims={"iss": "issuer-s", "sub": "subject-s", "aud": "terminal-bench", "iat": NOW - 4, "exp": NOW + 77, "scope": ["read", 5]})},
        {"id": "g6-scope-empty-entry", "cose": make_token(claims={"iss": "issuer-t", "sub": "subject-t", "aud": "terminal-bench", "iat": NOW - 4, "exp": NOW + 77, "scope": [""]})},
        "",
        {"id": "h8-protected-chunked", "cose": make_token(protected_indef=True)},
        {"id": "h9-signature-chunked", "cose": make_token(signature_indef=True)},
        {"id": "i0-protected-indef-map", "cose": make_token(protected_pairs=indefinite_protected)},
        {"id": "i1-payload-indef-map", "cose": make_token(payload_bytes=indefinite_payload)},
    ]


def corpus_classification_boundaries():
    missing_kid = [(1, -16)]
    missing_alg = [(4, b"alpha")]
    unknown_kid = [(1, -16), (4, b"omega")]
    protected = raw_map([(1, -16), (4, b"alpha")])
    return [
        '{"id": "bad-json", "cose": ',
        {"id": "json-missing-cose"},
        {"id": "json-numeric-cose", "cose": 7},
        {"id": 123, "cose": make_token()},
        {"id": "invalid-cbor", "cose": "58"},
        {"id": "shape-short", "cose": cbor([1, 2, 3]).hex()},
        {"id": "tag-wrong", "cose": (enc_len(6, 19) + bytes.fromhex(make_token(tag=False))).hex()},
        {"id": "nonbyte-protected", "cose": raw_cose(cbor(1), cbor(("map", [])), cbor(b"payload"), cbor(b"sig"))},
        {"id": "nonbyte-payload", "cose": raw_cose(cbor(protected), cbor(("map", [])), cbor(1), cbor(b"sig"))},
        {"id": "nonbyte-signature", "cose": raw_cose(cbor(protected), cbor(("map", [])), cbor(b"payload"), cbor(1))},
        {"id": "nonmap-unprotected", "cose": raw_cose(cbor(protected), cbor([]), cbor(b"payload"), cbor(b"sig"))},
        {"id": "alg-missing", "cose": make_token(protected_pairs=missing_alg)},
        {"id": "alg-wrong", "cose": make_token(protected_pairs=[(1, -7), (4, b"alpha")])},
        {"id": "k0-missing-kid", "cose": make_token(protected_pairs=missing_kid)},
        {"id": "k1-unknown-kid", "cose": make_token(protected_pairs=unknown_kid, signature_key="alpha")},
        {"id": "k2-missing-exp", "cose": make_token(claims={"iss": "issuer-x", "sub": "subject-x", "aud": "terminal-bench", "iat": NOW - 10, "scope": ["read"]})},
    ]


def corpus_alternate_keyring():
    return [
        {"id": "d0-alt-valid", "cose": make_token(kid="gamma", claims={"iss": "issuer-alt", "sub": "subject-alt", "aud": "terminal-bench", "iat": NOW - 8, "exp": NOW + 120, "scope": ["audit", "rotate"]}, keyring=ALT_KEYS)},
        {"id": "d1-alt-badsig", "cose": make_token(kid="delta", claims={"iss": "issuer-delta", "sub": "subject-delta", "aud": "terminal-bench", "iat": NOW - 8, "exp": NOW + 120, "scope": ["audit"]}, tamper=True, keyring=ALT_KEYS)},
        {"id": "d2-fixture-kid-not-present", "cose": make_token(kid="alpha", signature_key="gamma", keyring=ALT_KEYS)},
    ]


class D:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        if self.pos + n > len(self.data):
            raise ValueError("short")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def length(self, ai):
        if ai < 24:
            return ai
        if ai == 24:
            return self.read(1)[0]
        if ai == 25:
            return int.from_bytes(self.read(2), "big")
        if ai == 26:
            return int.from_bytes(self.read(4), "big")
        if ai == 27:
            return int.from_bytes(self.read(8), "big")
        raise ValueError("length")

    def value(self):
        ib = self.read(1)[0]
        major, ai = ib >> 5, ib & 31
        if major == 0:
            return self.length(ai)
        if major == 1:
            return -1 - self.length(ai)
        if major == 2:
            if ai == 31:
                chunks = []
                while self.data[self.pos] != 0xFF:
                    chunks.append(self.value())
                self.pos += 1
                if not all(isinstance(x, bytes) for x in chunks):
                    raise ValueError("chunk")
                return b"".join(chunks)
            return self.read(self.length(ai))
        if major == 3:
            if ai == 31:
                chunks = []
                while self.data[self.pos] != 0xFF:
                    chunks.append(self.value())
                self.pos += 1
                return "".join(chunks)
            return self.read(self.length(ai)).decode()
        if major == 4:
            return [self.value() for _ in range(self.length(ai))]
        if major == 5:
            last = None
            out = []
            seen = set()
            for _ in range(self.length(ai)):
                start = self.pos
                k = self.value()
                kb = self.data[start:self.pos]
                if last is not None and kb <= last:
                    raise ValueError("order")
                if kb in seen:
                    raise ValueError("repeat")
                seen.add(kb)
                last = kb
                out.append((k, self.value()))
            return dict(out)
        if major == 6:
            return ("tag", self.length(ai), self.value())
        if major == 7 and ai == 20:
            return False
        if major == 7 and ai == 21:
            return True
        if major == 7 and ai == 22:
            return None
        raise ValueError("type")


def dec(data):
    d = D(data)
    value = d.value()
    if d.pos != len(data):
        raise ValueError("trailing")
    return value


def chunked_protected_or_signature(data: bytes) -> bool:
    pos = 1 if data[:1] == b"\xd2" else 0
    if pos >= len(data) or data[pos] != 0x84:
        return False
    pos += 1
    if pos < len(data) and data[pos] == 0x5F:
        return True
    d = D(data)
    d.pos = pos
    d.value()
    d.value()
    d.value()
    return d.pos < len(data) and data[d.pos] == 0x5F


def reference_record(row, keys=KEYS):
    if isinstance(row, str):
        rid = ""
        try:
            j = json.loads(row)
            if isinstance(j, dict) and isinstance(j.get("id"), str):
                rid = j["id"]
        except Exception:
            pass
        return {"id": rid, "status": "malformed", "issuer": "", "subject": "", "audience": "", "expires": 0, "scopes": []}
    rid = row.get("id") if isinstance(row.get("id"), str) else ""
    base = {"id": rid, "status": "malformed", "issuer": "", "subject": "", "audience": "", "expires": 0, "scopes": []}
    try:
        if not isinstance(row.get("id"), str) or not isinstance(row.get("cose"), str):
            return base
        raw = bytes.fromhex(row["cose"])
        if chunked_protected_or_signature(raw):
            return base
        tok = dec(raw)
        if isinstance(tok, tuple):
            if tok[1] != 18:
                return base
            tok = tok[2]
        protected, unprotected, payload, sig = tok
        if not isinstance(protected, bytes) or not isinstance(payload, bytes) or not isinstance(sig, bytes):
            return base
        if not isinstance(unprotected, dict):
            return base
        pmap = dec(protected)
        claims = dec(payload)
        if cbor(("map", list(pmap.items()))) != protected or cbor(("map", list(claims.items()))) != payload:
            return base
        if unprotected:
            base["status"] = "unsupported"
            return base
        if any(label not in {1, 2, 4} for label in pmap):
            base["status"] = "unsupported"
            return base
        crit = pmap.get(2, [])
        if not isinstance(crit, list) or len(set(crit)) != len(crit) or any(not isinstance(label, int) or label not in {1, 2, 4} or label not in pmap for label in crit):
            base["status"] = "unsupported"
            return base
        if pmap.get(1) != -16 or not isinstance(pmap.get(4), bytes):
            base["status"] = "unsupported"
            return base
        kid = pmap[4].decode()
        if kid not in keys:
            base["status"] = "unsupported"
            return base
        calc = hmac.new(keys[kid], cbor(["Signature1", protected, b"", payload]), hashlib.sha256).digest()
        if calc != sig:
            base["status"] = "bad-signature"
            return base
        scope_claim = claims.get("scope")
        scope_ok = isinstance(scope_claim, list) and bool(scope_claim) and all(isinstance(s, str) and s for s in scope_claim)
        scopes = sorted(set(s for s in (scope_claim or []) if isinstance(s, str) and s))
        base.update({"issuer": claims.get("iss", ""), "subject": claims.get("sub", ""), "audience": claims.get("aud", ""), "expires": claims.get("exp", 0), "scopes": scopes})
        if claims.get("nbf", NOW) > NOW:
            base["status"] = "not-yet-valid"
        elif claims.get("exp", -1) <= NOW:
            base["status"] = "expired"
        elif not base["issuer"] or not base["subject"] or base["audience"] != "terminal-bench" or not scope_ok or type(claims.get("iat")) is not int or claims.get("iat") > NOW + 60:
            base["status"] = "bad-claims"
        else:
            base["status"] = "valid"
        return base
    except Exception:
        return base


def expected(rows, keys=KEYS):
    tokens = sorted([reference_record(r, keys) for r in rows], key=lambda x: x["id"])
    by_status = {s: 0 for s in STATUSES}
    for token in tokens:
        by_status[token["status"]] += 1
    return {"generated_at": NOW, "tokens": tokens, "summary": {"total": len(tokens), "valid": by_status["valid"], "invalid": len(tokens) - by_status["valid"], "by_status": by_status}}


def run_case(tmp_path, rows, keys=KEYS):
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True)
    token_path = tmp_path / "tokens.jsonl"
    keys_path = tmp_path / "keys.json"
    out_path = tmp_path / "report.json"
    token_path.write_text("".join((r if isinstance(r, str) else json.dumps(r, sort_keys=True)) + "\n" for r in rows))
    keys_path.write_text(json.dumps({k: v.hex() for k, v in keys.items()}, sort_keys=True))
    before = hashlib.sha256(token_path.read_bytes()).hexdigest(), hashlib.sha256(keys_path.read_bytes()).hexdigest()
    completed = subprocess.run(
        [str(BIN), "--tokens", str(token_path), "--keys", str(keys_path), "--out", str(out_path), "--time", str(NOW)],
        text=True,
        capture_output=True,
        check=True,
        cwd=str(APP),
    )
    assert completed.stdout == completed.stderr == ""
    after = hashlib.sha256(token_path.read_bytes()).hexdigest(), hashlib.sha256(keys_path.read_bytes()).hexdigest()
    assert before == after
    return json.loads(out_path.read_text())


def test_primary_report_matches_contract(tmp_path):
    """Covers all statuses, indefinite payload bytes, iat skew, and nbf-before-expired precedence."""
    rows = corpus_primary()
    assert run_case(tmp_path, rows) == expected(rows)


def test_secondary_report_is_independent_and_ordered(tmp_path):
    """Checks an independent corpus for duplicate protected keys, claims, scopes, and id ordering."""
    rows = corpus_secondary()
    report = run_case(tmp_path, rows)
    assert report == expected(rows)
    assert [t["id"] for t in report["tokens"]] == sorted(t["id"] for t in report["tokens"])


def test_hardened_header_and_claim_semantics(tmp_path):
    """Adds header hardening, blank-line handling, bad hex, untagged input, and scope typing."""
    rows = corpus_hardened()
    assert run_case(tmp_path, rows) == expected(rows)


def test_boundary_classifications_for_json_kid_and_expiry(tmp_path):
    """Covers malformed JSON rows, valid-JSON malformed rows, kid rejection, and missing-exp expiry."""
    rows = corpus_classification_boundaries()
    assert run_case(tmp_path, rows) == expected(rows)


def test_keys_are_loaded_from_runtime_file(tmp_path):
    """Uses a disjoint keyring so fixture-key hardcoding fails deterministically."""
    rows = corpus_alternate_keyring()
    assert run_case(tmp_path, rows, keys=ALT_KEYS) == expected(rows, keys=ALT_KEYS)


def test_fixture_tree_is_not_rewritten(tmp_path):
    """Verifies the CLI writes only the requested report and never rewrites token or key inputs."""
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True)
    source_tokens = APP / "fixtures" / "tokens.jsonl"
    source_keys = APP / "fixtures" / "keys.json"
    local_tokens = tmp_path / "copy.jsonl"
    local_keys = tmp_path / "copy-keys.json"
    shutil.copyfile(source_tokens, local_tokens)
    shutil.copyfile(source_keys, local_keys)
    if not local_tokens.read_text().strip():
        local_tokens.write_text(json.dumps(corpus_primary()[0], sort_keys=True) + "\n")
    before = local_tokens.read_bytes(), local_keys.read_bytes()
    completed = subprocess.run(
        [str(BIN), "--tokens", str(local_tokens), "--keys", str(local_keys), "--out", str(tmp_path / "out.json"), "--time", str(NOW)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stdout == completed.stderr == ""
    assert (local_tokens.read_bytes(), local_keys.read_bytes()) == before
