import hashlib
import json
import stat
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

APP = Path("/app/certgate")
BIN = APP / "bin/certgate"


def length(n: int) -> bytes:
    if n < 128:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + length(len(value)) + value


def integer(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if raw[0] & 0x80:
        raw = b"\0" + raw
    return tlv(0x02, raw)


def text(tag: int, value: str) -> bytes:
    return tlv(tag, value.encode("ascii"))


def key_for(subject: str) -> bytes:
    return hashlib.sha256(("certgate-key:" + subject).encode()).digest()


def base_specs() -> list[dict]:
    return [
        {
            "serial": 11, "issuer": "Intermediate", "subject": "Leaf", "before": 100,
            "after": 300, "ca": False, "path": 0, "usage": 0x80,
            "dns": ["api.svc.example.com", "db.svc.example.com"], "permit": [], "exclude": [],
        },
        {
            "serial": 22, "issuer": "Root", "subject": "Intermediate", "before": 50,
            "after": 400, "ca": True, "path": 0, "usage": 0x04, "dns": [],
            "permit": ["svc.example.com"], "exclude": ["blocked.svc.example.com"],
        },
        {
            "serial": 33, "issuer": "Root", "subject": "Root", "before": 1,
            "after": 500, "ca": True, "path": 1, "usage": 0x04, "dns": [],
            "permit": ["example.com"], "exclude": ["evil.example.com"],
        },
    ]


def encode_list(tag: int, values: list[str]) -> bytes:
    seq = tlv(0x30, b"".join(text(0x16, item) for item in values))
    return seq if tag == 0x30 else tlv(tag, seq)


def encode_cert(spec: dict, issuer_key: bytes) -> bytes:
    validity = tlv(
        0x30,
        integer(spec["before"]) + integer(spec["after"]) + spec.get("validity_extra", b""),
    )
    boolean = spec.get("boolean_raw", b"\xff" if spec["ca"] else b"\x00")
    bit_string = spec.get("bit_string_raw", bytes([0, spec["usage"]]))
    extensions = tlv(
        0x30,
        tlv(0x01, boolean)
        + integer(spec["path"])
        + tlv(0x03, bit_string)
        + spec.get("dns_encoded", encode_list(0x30, spec["dns"]))
        + spec.get("permit_encoded", encode_list(0xA0, spec["permit"]))
        + spec.get("exclude_encoded", encode_list(0xA1, spec["exclude"])),
    )
    subject_key = spec.get("key", key_for(spec["subject"]))
    authority = spec.get("authority", issuer_key)
    body = (
        spec.get("version_raw", integer(1))
        + spec.get("serial_raw", integer(spec["serial"]))
        + text(0x0C, spec["issuer"])
        + text(0x0C, spec["subject"])
        + validity
        + extensions
        + tlv(0x04, subject_key)
        + tlv(0x04, authority)
    )
    signature = hashlib.sha256(body + issuer_key).digest()
    if spec.get("bad_signature"):
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    signature = spec.get("signature_raw", signature)
    return tlv(0x30, body + tlv(0x04, signature) + spec.get("certificate_extra", b""))


def encode_chain(specs: list[dict]) -> bytes:
    result = []
    for i, spec in enumerate(specs):
        issuer_key = key_for(specs[i + 1]["subject"]) if i + 1 < len(specs) else key_for(spec["subject"])
        result.append(encode_cert(spec, issuer_key))
    return b"".join(result)


def policy_text(specs: list[dict], **changes: str | int) -> str:
    values = {
        "time": 200,
        "trust_subject": specs[-1]["subject"],
        "trust_key": key_for(specs[-1]["subject"]).hex(),
        "dns_suffix": "example.com",
    }
    values.update(changes)
    order = changes.pop("_order", None) if "_order" in changes else None
    keys = order or ["time", "trust_subject", "trust_key", "dns_suffix"]
    return "".join(f"{key}={values[key]}\n" for key in keys)


def invoke(tmp_path: Path, specs=None, bundle=None, policy=None, stale=False, argument_order=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    specs = deepcopy(base_specs() if specs is None else specs)
    chain_path = tmp_path / "chain.der"
    policy_path = tmp_path / "policy.conf"
    out = tmp_path / "report"
    chain_path.write_bytes(encode_chain(specs) if bundle is None else bundle)
    policy_data = policy if policy is not None else policy_text(specs)
    if isinstance(policy_data, bytes):
        policy_path.write_bytes(policy_data)
    else:
        policy_path.write_text(policy_data)
    if stale:
        out.mkdir()
        (out / "stale.txt").write_text("must disappear")
        nested = out / "old"
        nested.mkdir()
        (nested / "data").write_text("old")
        old_out_inode = out.stat().st_ino
    mapping = {"policy": policy_path, "chain": chain_path, "out": out}
    order = argument_order or ["policy", "chain", "out"]
    command = [str(BIN)]
    for name in order:
        command.extend([f"--{name}", str(mapping[name])])
    before = (chain_path.read_bytes(), policy_path.read_bytes())
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if stale:
        completed.old_out_inode = old_out_inode
    after = (chain_path.read_bytes(), policy_path.read_bytes())
    report = json.loads((out / "decision.json").read_text()) if (out / "decision.json").exists() else None
    return completed, out, report, before, after


def assert_reject(tmp_path: Path, code: str, specs=None, bundle=None):
    completed, out, report, before, after = invoke(tmp_path, specs=specs, bundle=bundle)
    assert completed.returncode == 2, completed.stderr
    assert completed.stdout == completed.stderr == ""
    assert report["decision"] == "REJECT"
    assert report["code"] == code
    assert_output_files(out)
    assert before == after
    return out, report


def assert_output_files(out: Path):
    entries = list(out.iterdir())
    assert {entry.name for entry in entries} == {"decision.json", "trace.tsv"}
    for entry in entries:
        assert stat.S_ISREG(entry.lstat().st_mode)
        assert not entry.is_symlink()


@pytest.fixture(scope="session", autouse=True)
def rebuild_tool():
    """The documented build entry point must produce the evaluator from local sources."""
    completed = subprocess.run([str(APP / "scripts/build.sh")], text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert BIN.is_file()


def test_valid_chain_replaces_output_and_is_deterministic(tmp_path):
    """A valid chain is accepted with exact reports, immutable inputs, and no stale artifacts."""
    completed, out, report, before, after = invoke(tmp_path, stale=True, argument_order=["out", "chain", "policy"])
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == completed.stderr == ""
    assert report == {"decision": "ACCEPT", "code": "OK", "certificates": 3, "leaf": "Leaf"}
    assert (out / "decision.json").read_text() == (
        '{"decision":"ACCEPT","code":"OK","certificates":3,"leaf":"Leaf"}\n'
    )
    assert_output_files(out)
    assert completed.old_out_inode != out.stat().st_ino
    assert before == after
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    completed2, out2, _, _, _ = invoke(tmp_path, stale=False)
    assert completed2.returncode == 0
    assert first == {p.name: p.read_bytes() for p in out2.iterdir()}
    lines = (out / "trace.tsv").read_text().splitlines()
    assert lines[0] == "index\tsubject\tissuer\trole\ttime\tsignature\tconstraints"
    assert lines[1:] == [
        "0\tLeaf\tIntermediate\tleaf\tok\tok\tok",
        "1\tIntermediate\tRoot\tca\tok\tok\tok",
        "2\tRoot\tRoot\tca\tok\tok\tok",
    ]
    assert (out / "trace.tsv").read_bytes().endswith(b"\n")


def test_nonminimal_lengths_truncation_and_stray_bytes_are_der_errors(tmp_path):
    """Outer boundaries and shortest-form lengths are enforced before chain policy."""
    good = encode_chain(base_specs())
    first_len_count = good[1] & 0x7F
    content_len = int.from_bytes(good[2 : 2 + first_len_count], "big")
    content = good[2 + first_len_count : 2 + first_len_count + content_len]
    rest = good[2 + first_len_count + content_len :]
    nonminimal = b"\x30\x83\x00" + content_len.to_bytes(2, "big") + content + rest
    assert_reject(tmp_path / "a", "DER", bundle=nonminimal)
    assert_reject(tmp_path / "b", "DER", bundle=good[:-1])
    assert_reject(tmp_path / "c", "DER", bundle=good + b"\x00")


def test_scalar_der_encodings_are_canonical(tmp_path):
    """INTEGER, BOOLEAN, and BIT STRING canonical forms are independently rejected."""
    specs = base_specs()
    specs[0]["version_raw"] = tlv(0x02, b"\x00\x01")
    assert_reject(tmp_path / "integer", "DER", specs=specs)
    specs = base_specs()
    specs[1]["boolean_raw"] = b"\x01"
    assert_reject(tmp_path / "boolean", "DER", specs=specs)
    specs = base_specs()
    specs[0]["bit_string_raw"] = b"\x01\x80"
    assert_reject(tmp_path / "bits", "DER", specs=specs)
    specs = base_specs()
    specs[0]["version_raw"] = tlv(0x02, b"\xff")
    assert_reject(tmp_path / "negative", "DER", specs=specs)
    specs = base_specs()
    specs[0]["version_raw"] = tlv(0x02, b"\x01" + b"\0" * 8)
    assert_reject(tmp_path / "nine-octet", "DER", specs=specs)
    assert_reject(tmp_path / "indefinite", "DER", bundle=b"\x30\x80\0\0")


def test_nested_values_and_bundle_limits_are_bounded(tmp_path):
    """Nested containers require exact consumption and list and chain bounds are applied."""
    specs = base_specs()
    specs[0]["validity_extra"] = tlv(0x05, b"")
    assert_reject(tmp_path / "nested", "DER", specs=specs)
    specs = base_specs()
    permitted_sequence = tlv(0x30, text(0x16, "svc.example.com"))
    specs[1]["permit_encoded"] = tlv(0xA0, permitted_sequence + tlv(0x05, b""))
    assert_reject(tmp_path / "context", "DER", specs=specs)
    specs = base_specs()
    specs[0]["certificate_extra"] = tlv(0x05, b"")
    assert_reject(tmp_path / "certificate-trailing", "DER", specs=specs)
    specs = base_specs()
    specs[0]["dns"] = [f"n{i}.example.com" for i in range(9)]
    assert_reject(tmp_path / "list", "DER", specs=specs)
    specs = base_specs()
    specs[1]["permit"] = [f"p{i}.example.com" for i in range(9)]
    assert_reject(tmp_path / "permit-list", "DER", specs=specs)
    specs = base_specs()
    specs[2]["exclude"] = [f"x{i}.example.com" for i in range(9)]
    assert_reject(tmp_path / "exclude-list", "DER", specs=specs)
    specs = make_deep_chain(9)
    assert_reject(tmp_path / "count", "DER", specs=specs)
    matching_name = ".".join(["a" * 63, "a" * 63, "a" * 63, "b" * 49, "example", "com"])
    other_name = ".".join(["z" * 63, "z" * 63, "z" * 63, "y" * 61])
    specs = base_specs()
    specs[0]["dns"] = [matching_name]
    specs[1]["permit"] = ["example.com"]
    specs[2]["permit"] = [matching_name] * 8
    specs[2]["exclude"] = [other_name] * 8
    oversized_root = encode_cert(specs[2], key_for(specs[2]["subject"]))
    assert len(oversized_root) > 4096
    assert_reject(tmp_path / "cert-size", "DER", specs=specs)
    specs[2]["exclude"] = [other_name] * 7
    boundary_root = encode_cert(specs[2], key_for(specs[2]["subject"]))
    assert 4000 <= len(boundary_root) <= 4096
    assert invoke(tmp_path / "cert-boundary", specs=specs)[0].returncode == 0
    specs = make_deep_chain(8)
    names = [f"N{i}" + "x" * 61 for i in range(8)]
    for index, spec in enumerate(specs):
        spec["subject"] = names[index]
        spec["issuer"] = names[index + 1] if index + 1 < len(specs) else names[index]
    specs[0]["dns"] = [matching_name] * 8
    for spec in specs[1:]:
        spec["permit"] = [matching_name] * 8
        spec["exclude"] = [other_name] * 8
    oversized_bundle = encode_chain(specs)
    assert len(oversized_bundle) > 32768
    assert_reject(tmp_path / "bundle-size", "DER", specs=specs, bundle=oversized_bundle)
    assert_reject(tmp_path / "length-width", "DER", bundle=b"\x30\x84\x00\x00\x00\x01x")


def test_schema_ranges_and_identifiers_are_enforced(tmp_path):
    """Narrow schema ranges and the identifier alphabet are part of bundle decoding."""
    specs = base_specs()
    specs[0]["serial"] = 2147483648
    assert_reject(tmp_path / "serial", "DER", specs=specs)
    specs = base_specs()
    specs[0]["serial"] = 0
    assert_reject(tmp_path / "zero-serial", "DER", specs=specs)
    specs = base_specs()
    specs[0]["version_raw"] = integer(2)
    assert_reject(tmp_path / "version", "DER", specs=specs)
    specs = base_specs()
    specs[1]["subject"] = "bad subject"
    specs[0]["issuer"] = "bad subject"
    assert_reject(tmp_path / "identifier", "DER", specs=specs)
    specs = base_specs()
    specs[2]["key"] = b"k" * 31
    assert_reject(tmp_path / "key-size", "DER", specs=specs)
    for size in (31, 33):
        specs = base_specs()
        specs[1]["authority"] = b"a" * size
        assert_reject(tmp_path / f"authority-{size}", "DER", specs=specs)
        specs = base_specs()
        specs[1]["signature_raw"] = b"s" * size
        assert_reject(tmp_path / f"signature-{size}", "DER", specs=specs)
    specs = base_specs()
    specs[1]["path"] = 8
    assert_reject(tmp_path / "path-range", "DER", specs=specs)
    specs = base_specs()
    specs[0]["issuer"] = specs[1]["subject"] = ""
    assert_reject(tmp_path / "identifier-empty", "DER", specs=specs)
    specs = base_specs()
    root = "R" * 63
    specs[1]["issuer"] = root
    specs[2]["issuer"] = specs[2]["subject"] = root
    completed, _, report, _, _ = invoke(tmp_path / "identifier-max", specs=specs)
    assert completed.returncode == 0 and report["decision"] == "ACCEPT"
    specs = base_specs()
    specs[0]["issuer"] = specs[1]["subject"] = "R" * 64
    assert_reject(tmp_path / "identifier-long", "DER", specs=specs)
    specs = make_deep_chain(1)
    specs[0]["issuer"] = specs[0]["subject"] = "R"
    assert invoke(tmp_path / "identifier-min", specs=specs)[0].returncode == 0


def test_roles_usage_and_unknown_bits_are_checked(tmp_path):
    """Leaf and CA roles bind basic constraints, path fields, SAN placement, and exact usage bits."""
    for name, mutate in [
        ("leaf-ca", lambda s: s[0].update(ca=True)),
        ("leaf-path", lambda s: s[0].update(path=1)),
        ("leaf-no-san", lambda s: s[0].update(dns=[])),
        ("leaf-constraint", lambda s: s[0].update(permit=["example.com"])),
        ("ca-san", lambda s: s[1].update(dns=["ca.example.com"])),
        ("not-ca", lambda s: s[1].update(ca=False)),
        ("usage", lambda s: s[1].update(usage=0x84)),
    ]:
        specs = base_specs()
        mutate(specs)
        assert_reject(tmp_path / name, "ROLE", specs=specs)


def test_duplicate_identities_and_serials_are_rejected(tmp_path):
    """Subject names and serial numbers are independently unique over the chain."""
    specs = base_specs()
    specs[1]["serial"] = specs[0]["serial"]
    assert_reject(tmp_path / "serial", "DUPLICATE", specs=specs)
    specs = base_specs()
    specs[1]["subject"] = "Root"
    specs[0]["issuer"] = "Root"
    assert_reject(tmp_path / "subject", "DUPLICATE", specs=specs)


def test_validity_is_inclusive_and_rejects_bad_intervals(tmp_path):
    """Evaluation at both endpoints succeeds while expired and inverted intervals fail."""
    specs = base_specs()
    for spec in specs:
        spec["before"] = 0
    policy = policy_text(specs, time=0)
    assert invoke(tmp_path / "zero", specs=specs, policy=policy)[0].returncode == 0
    specs = base_specs()
    policy = policy_text(specs, time=100)
    assert invoke(tmp_path / "edge", specs=specs, policy=policy)[0].returncode == 0
    policy = policy_text(specs, time=300)
    assert invoke(tmp_path / "upper-edge", specs=specs, policy=policy)[0].returncode == 0
    specs = base_specs()
    for spec in specs:
        spec["before"] = 0
        spec["after"] = 9223372036854775807
    maximum = policy_text(specs, time=9223372036854775807)
    assert invoke(tmp_path / "maximum", specs=specs, policy=maximum)[0].returncode == 0
    specs = base_specs()
    specs[1]["after"] = 199
    out, _ = assert_reject(tmp_path / "expired", "TIME", specs=specs)
    assert (out / "trace.tsv").read_text().splitlines()[2].split("\t")[4] == "fail"
    specs = base_specs()
    specs[2]["before"], specs[2]["after"] = 300, 200
    assert_reject(tmp_path / "inverted", "TIME", specs=specs)


def test_name_and_key_links_are_both_required(tmp_path):
    """Every adjacency and the self-issued root bind issuer names and authority keys."""
    specs = base_specs()
    specs[0]["issuer"] = "Other"
    assert_reject(tmp_path / "name", "LINK", specs=specs)
    specs = base_specs()
    specs[1]["authority"] = b"x" * 32
    assert_reject(tmp_path / "key", "LINK", specs=specs)
    specs = base_specs()
    specs[2]["issuer"] = "Elsewhere"
    assert_reject(tmp_path / "root", "LINK", specs=specs)
    specs = base_specs()
    specs[2]["authority"] = b"x" * 32
    assert_reject(tmp_path / "root-key", "LINK", specs=specs)


def test_each_signature_covers_exact_encoded_body(tmp_path):
    """Tampering with a correctly linked certificate is caught by its issuer-key digest."""
    specs = base_specs()
    specs[1]["bad_signature"] = True
    out, _ = assert_reject(tmp_path, "SIGNATURE", specs=specs)
    rows = (out / "trace.tsv").read_text().splitlines()
    assert rows[2].split("\t")[5] == "fail"
    assert rows[1].split("\t")[5] == rows[3].split("\t")[5] == "ok"
    for name, index in [("leaf", 0), ("root", 2)]:
        specs = base_specs()
        specs[index]["bad_signature"] = True
        assert_reject(tmp_path / name, "SIGNATURE", specs=specs)


def test_path_length_counts_subordinate_cas(tmp_path):
    """Path-length limits use the number of lower CA certificates, not chain depth."""
    specs = base_specs()
    specs[2]["path"] = 0
    assert_reject(tmp_path, "PATHLEN", specs=specs)


def test_dns_syntax_and_policy_suffix_cover_every_leaf_name(tmp_path):
    """DNS syntax and the policy suffix apply to each SAN with label-boundary matching."""
    specs = base_specs()
    specs[0]["dns_encoded"] = tlv(0x30, text(0x16, ""))
    assert_reject(tmp_path / "empty-san", "DER", specs=specs)
    specs = base_specs()
    specs[0]["dns_encoded"] = tlv(0x30, tlv(0x16, b"api\0svc.example.com"))
    assert_reject(tmp_path / "nul-san", "DER", specs=specs)
    specs = base_specs()
    specs[0]["dns"][0] = "Bad.example.com"
    assert_reject(tmp_path / "syntax", "NAME", specs=specs)
    specs = base_specs()
    specs[0]["dns"].append("notexample.com")
    assert_reject(tmp_path / "boundary", "NAME", specs=specs)
    label = "a" * 63
    longest = ".".join([label, label, label, "b" * 61])
    specs = base_specs()
    specs[0]["dns"] = [longest]
    specs[1]["permit"] = []
    specs[2]["permit"] = []
    policy = policy_text(specs, dns_suffix=longest)
    assert invoke(tmp_path / "dns-max", specs=specs, policy=policy)[0].returncode == 0
    specs[0]["dns"] = ["a" * 64 + ".example.com"]
    assert_reject(tmp_path / "label-long", "NAME", specs=specs)
    specs[0]["dns"] = [longest + "x"]
    assert_reject(tmp_path / "dns-long", "DER", specs=specs)


def test_permitted_and_excluded_constraints_accumulate(tmp_path):
    """Each CA's permitted set and every excluded suffix constrain every leaf SAN."""
    specs = base_specs()
    specs[1]["permit"] = ["other.example.com"]
    assert_reject(tmp_path / "permit", "NAME", specs=specs)
    specs = base_specs()
    specs[2]["exclude"] = ["svc.example.com"]
    out, _ = assert_reject(tmp_path / "exclude", "NAME", specs=specs)
    assert (out / "trace.tsv").read_text().splitlines()[3].endswith("\tfail")
    specs = base_specs()
    specs[2]["permit"] = ["other.example.com"]
    assert_reject(tmp_path / "root-permit", "NAME", specs=specs)
    specs = base_specs()
    specs[1]["exclude"] = ["svc.example.com"]
    assert_reject(tmp_path / "intermediate-exclude", "NAME", specs=specs)
    specs = base_specs()
    specs[1]["permit"] = [f"unused{i}.example.com" for i in range(7)] + ["svc.example.com"]
    specs[2]["exclude"] = [f"unused{i}.example.com" for i in range(8)]
    assert invoke(tmp_path / "list-boundary", specs=specs)[0].returncode == 0
    specs = base_specs()
    specs[1]["permit"] = ["Bad.example.com"]
    assert_reject(tmp_path / "malformed-permit", "NAME", specs=specs)
    specs = base_specs()
    specs[2]["exclude"] = ["bad_.example.com"]
    assert_reject(tmp_path / "malformed-exclude", "NAME", specs=specs)


def test_trust_requires_both_anchor_subject_and_key(tmp_path):
    """A structurally valid self-signed root still needs the configured name and key."""
    specs = base_specs()
    wrong_name = policy_text(specs, trust_subject="OtherRoot")
    assert invoke(tmp_path / "name", specs=specs, policy=wrong_name)[2]["code"] == "TRUST"
    wrong_key = policy_text(specs, trust_key=(b"z" * 32).hex())
    completed, _, report, _, _ = invoke(tmp_path / "key", specs=specs, policy=wrong_key)
    assert completed.returncode == 2 and report["code"] == "TRUST"


def make_deep_chain(count: int) -> list[dict]:
    specs = []
    names = ["Root"] if count == 1 else ["Leaf"] + [f"CA{i}" for i in range(1, count - 1)] + ["Root"]
    for i, subject in enumerate(names):
        ca = i > 0
        issuer = names[i + 1] if i + 1 < count else subject
        specs.append({
            "serial": i + 1, "issuer": issuer, "subject": subject, "before": 1, "after": 500,
            "ca": ca, "path": max(0, i - 1), "usage": 0x04 if ca else 0x80,
            "dns": [] if ca else ["deep.example.com"], "permit": [], "exclude": [],
        })
    return specs


def test_eight_certificate_chain_and_ordering(tmp_path):
    """The maximum chain depth is accepted, while reversing valid members breaks leaf-first linkage."""
    specs = make_deep_chain(8)
    completed, _, report, _, _ = invoke(tmp_path / "deep", specs=specs)
    assert completed.returncode == 0 and report["certificates"] == 8
    reordered = base_specs()
    reordered[0], reordered[1] = reordered[1], reordered[0]
    assert_reject(tmp_path / "order", "ROLE", specs=reordered)


def test_single_self_issued_leaf_is_the_lower_bundle_boundary(tmp_path):
    """A one-certificate bundle can be both leaf and configured self-issued trust anchor."""
    specs = make_deep_chain(1)
    completed, out, report, _, _ = invoke(tmp_path, specs=specs)
    assert completed.returncode == 0
    assert report == {"decision": "ACCEPT", "code": "OK", "certificates": 1, "leaf": "Root"}
    assert len((out / "trace.tsv").read_text().splitlines()) == 2


def test_der_rejection_reports_decoded_prefix_and_replaces_stale_output(tmp_path):
    """A malformed suffix reports only the decoded prefix while still publishing atomically."""
    specs = base_specs()
    first = encode_chain(specs)[: len(encode_cert(specs[0], key_for(specs[1]["subject"])))]
    completed, out, report, before, after = invoke(tmp_path, bundle=first + b"\0", stale=True)
    assert completed.returncode == 2 and before == after
    assert report == {"decision": "REJECT", "code": "DER", "certificates": 1, "leaf": "Leaf"}
    assert_output_files(out)
    assert completed.old_out_inode != out.stat().st_ino
    assert len((out / "trace.tsv").read_text().splitlines()) == 2
    first_report = {p.name: p.read_bytes() for p in out.iterdir()}
    assert first_report["decision.json"] == (
        b'{"decision":"REJECT","code":"DER","certificates":1,"leaf":"Leaf"}\n'
    )
    assert first_report["trace.tsv"].endswith(b"\n") and not first_report["trace.tsv"].endswith(b"\n\n")
    completed, out, report, _, _ = invoke(tmp_path, bundle=first + b"\0")
    assert completed.returncode == 2 and report["code"] == "DER"
    assert first_report == {p.name: p.read_bytes() for p in out.iterdir()}
    empty_out, empty_report = assert_reject(tmp_path / "empty", "DER", bundle=b"")
    assert empty_report["certificates"] == 0 and empty_report["leaf"] == ""
    assert (empty_out / "trace.tsv").read_text() == (
        "index\tsubject\tissuer\trole\ttime\tsignature\tconstraints\n"
    )


def test_rejection_priority_is_stable_across_multiple_faults(tmp_path):
    """The documented priority determines the code when independent defects coexist."""
    specs = base_specs()
    specs[0].update(ca=True, usage=0x04, after=10, bad_signature=True)
    policy = policy_text(specs, trust_subject="Wrong")
    completed, _, report, _, _ = invoke(tmp_path, specs=specs, policy=policy)
    assert completed.returncode == 2 and report["code"] == "ROLE"
    cases = []
    specs = base_specs()
    specs[0].update(ca=True, usage=0x04)
    cases.append(("DER", specs, encode_chain(specs) + b"\0", {}))
    specs = base_specs()
    specs[0].update(ca=True, usage=0x04)
    specs[1]["serial"] = specs[0]["serial"]
    cases.append(("ROLE", specs, None, {}))
    specs = base_specs()
    specs[1]["serial"] = specs[0]["serial"]
    specs[0]["after"] = 10
    cases.append(("DUPLICATE", specs, None, {}))
    specs = base_specs()
    specs[0].update(after=10, issuer="Other")
    cases.append(("TIME", specs, None, {}))
    specs = base_specs()
    specs[0].update(issuer="Other", bad_signature=True)
    cases.append(("LINK", specs, None, {}))
    specs = base_specs()
    specs[0]["bad_signature"] = True
    specs[2]["path"] = 0
    cases.append(("SIGNATURE", specs, None, {}))
    specs = base_specs()
    specs[2]["path"] = 0
    specs[0]["dns"].append("outside.test")
    cases.append(("PATHLEN", specs, None, {}))
    specs = base_specs()
    specs[0]["dns"].append("outside.test")
    cases.append(("NAME", specs, None, {"trust_subject": "Wrong"}))
    for index, (expected, candidate, bundle, policy_changes) in enumerate(cases):
        policy = policy_text(candidate, **policy_changes)
        completed, _, report, _, _ = invoke(
            tmp_path / f"pair-{index}", specs=candidate, bundle=bundle, policy=policy
        )
        assert completed.returncode == 2 and report["code"] == expected


def test_invalid_policy_is_an_operational_error(tmp_path):
    """Duplicate or unknown policy keys fail operationally rather than becoming evidence decisions."""
    specs = base_specs()
    malformed = policy_text(specs) + "time=200\n"
    completed, out, report, _, _ = invoke(tmp_path / "duplicate", specs=specs, policy=malformed)
    assert completed.returncode == 1 and completed.stdout == completed.stderr == ""
    assert report is None and not out.exists()
    unknown = policy_text(specs).replace("dns_suffix=", "suffix=")
    completed, out, report, _, _ = invoke(tmp_path / "unknown", specs=specs, policy=unknown)
    assert completed.returncode == 1 and report is None and not out.exists()
    for index, bad in enumerate(
        [
            policy_text(specs).replace("time=200", "time=0200"),
            policy_text(specs).replace("time=200", "time=9223372036854775808"),
            policy_text(specs).replace("dns_suffix=example.com", "dns_suffix=-example.com"),
            policy_text(specs, trust_key="A" + key_for("Root").hex()[1:]),
            policy_text(specs, trust_key=key_for("Root").hex()[:-1]),
            policy_text(specs, trust_key=key_for("Root").hex() + "0"),
            policy_text(specs).replace("trust_subject=Root", "trust_subject=bad subject"),
            policy_text(specs) + "\n",
            policy_text(specs).replace("time=200\n", "time=200\r\n"),
        ]
    ):
        completed, out, report, _, _ = invoke(tmp_path / f"strict-{index}", specs=specs, policy=bad)
        assert completed.returncode == 1 and completed.stdout == completed.stderr == ""
        assert report is None and not out.exists()
    embedded_nul = policy_text(specs).encode().replace(b"time=200", b"time=200\0junk")
    completed, out, report, _, _ = invoke(tmp_path / "nul", specs=specs, policy=embedded_nul)
    assert completed.returncode == 1 and report is None and not out.exists()
    invalid_utf8 = policy_text(specs).encode().replace(b"trust_subject=Root", b"trust_subject=R\xffot")
    completed, out, report, _, _ = invoke(tmp_path / "utf8", specs=specs, policy=invalid_utf8)
    assert completed.returncode == 1 and report is None and not out.exists()
    no_final_newline = policy_text(specs).rstrip("\n")
    assert invoke(tmp_path / "no-newline", specs=specs, policy=no_final_newline)[0].returncode == 0
    reordered = policy_text(
        specs,
        _order=["dns_suffix", "trust_key", "time", "trust_subject"],
    )
    assert invoke(tmp_path / "reordered", specs=specs, policy=reordered)[0].returncode == 0


def test_bad_cli_is_an_operational_error(tmp_path):
    """Missing and duplicate command options return the documented operational status."""
    completed = subprocess.run([str(BIN)], text=True, capture_output=True, check=False)
    assert completed.returncode == 1 and completed.stdout == completed.stderr == ""
    completed = subprocess.run(
        [str(BIN), "--policy", "x", "--policy", "y", "--out", str(tmp_path / "out")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1 and not (tmp_path / "out").exists()
    completed = subprocess.run(
        [str(BIN), "--policy", "x", "--chain", "y", "--unknown", str(tmp_path / "out")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1 and not (tmp_path / "out").exists()
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("same")
    completed = subprocess.run(
        [str(BIN), "--policy", "x", "--chain", "y", "--out", str(existing), "extra"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1 and completed.stdout == completed.stderr == ""
    assert [p.name for p in existing.iterdir()] == ["keep"] and (existing / "keep").read_text() == "same"
    specs = base_specs()
    bad_policy = policy_text(specs) + "unknown=value\n"
    completed, out, report, _, _ = invoke(tmp_path / "unchanged", specs=specs, policy=bad_policy, stale=True)
    assert completed.returncode == 1 and completed.stdout == completed.stderr == "" and report is None
    assert (out / "stale.txt").read_text() == "must disappear"
    assert (out / "old" / "data").read_text() == "old"
    missing = tmp_path / "missing"
    output = tmp_path / "missing-parent" / "report"
    chain = tmp_path / "chain.der"
    chain.write_bytes(encode_chain(specs))
    completed = subprocess.run(
        [str(BIN), "--policy", str(missing), "--chain", str(chain), "--out", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1 and completed.stdout == completed.stderr == "" and not output.exists()
    policy = tmp_path / "policy.conf"
    policy.write_text(policy_text(specs))
    io_out = tmp_path / "io-existing"
    io_out.mkdir()
    (io_out / "keep").write_text("same")
    completed = subprocess.run(
        [str(BIN), "--policy", str(policy), "--chain", str(missing), "--out", str(io_out)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1 and completed.stdout == completed.stderr == ""
    assert (io_out / "keep").read_text() == "same"
    completed = subprocess.run(
        [str(BIN), "--policy", str(policy), "--chain", str(chain), "--out", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1 and not output.exists()
