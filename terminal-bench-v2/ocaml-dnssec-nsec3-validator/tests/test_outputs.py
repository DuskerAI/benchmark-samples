import base64
import hashlib
import json
import subprocess

import pytest

ALPHA = "0123456789ABCDEFGHIJKLMNOPQRSTUV"
ORIGIN = "example."
ZERO = "0" * 32
TOP = "V" * 32


def decode_name(text, origin=ORIGIN):
    """Return lower-case DNS labels with presentation escapes interpreted."""
    if text == "@":
        return decode_name(origin, origin)
    absolute = text.endswith(".")
    body = text[:-1] if absolute else text
    labels = []
    current = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == ".":
            labels.append(bytes(current).decode("latin1").lower())
            current.clear()
            i += 1
        elif ch == "\\":
            if i + 3 < len(body) and body[i + 1 : i + 4].isdigit():
                current.append(int(body[i + 1 : i + 4]))
                i += 4
            elif i + 1 < len(body):
                current.extend(body[i + 1].encode("latin1"))
                i += 2
            else:
                current.append(ord("\\"))
                i += 1
        else:
            current.extend(ch.lower().encode("latin1"))
            i += 1
    if body or absolute:
        labels.append(bytes(current).decode("latin1").lower())
    if not absolute:
        labels.extend(decode_name(origin, origin))
    return [label for label in labels if label]


def present(labels):
    """Format canonical labels as an absolute lower-case name."""
    return ".".join(labels) + "."


def wire(labels):
    """Encode labels in DNS wire form."""
    out = bytearray()
    for label in labels:
        data = label.encode("latin1")
        out.append(len(data))
        out.extend(data)
    out.append(0)
    return bytes(out)


def salt_bytes(salt):
    """Decode NSEC3 salt text."""
    return b"" if salt == "-" else bytes.fromhex(salt)


def nsec3_hash(name, iterations=0, salt="-", origin=ORIGIN):
    """Compute an NSEC3 SHA-1/base32hex owner hash."""
    salt_b = salt_bytes(salt)
    digest = hashlib.sha1(wire(decode_name(name, origin)) + salt_b).digest()
    for _ in range(iterations):
        digest = hashlib.sha1(digest + salt_b).digest()
    return base64.b32hexencode(digest).decode().rstrip("=")


def predecessor_hash(value):
    """Return a fixed-width base32hex value immediately before the input."""
    chars = list(value)
    for index in range(len(chars) - 1, -1, -1):
        pos = ALPHA.index(chars[index])
        if pos > 0:
            chars[index] = ALPHA[pos - 1]
            chars[index + 1 :] = [ALPHA[-1]] * (len(chars) - index - 1)
            return "".join(chars)
    raise ValueError("no predecessor for zero hash")


def successor_hash(value):
    """Return a fixed-width base32hex value immediately after the input."""
    chars = list(value)
    for index in range(len(chars) - 1, -1, -1):
        pos = ALPHA.index(chars[index])
        if pos < len(ALPHA) - 1:
            chars[index] = ALPHA[pos + 1]
            chars[index + 1 :] = [ALPHA[0]] * (len(chars) - index - 1)
            return "".join(chars)
    raise ValueError("no successor for top hash")


def nsec3(owner_hash, flags, iterations, salt, next_hash, types):
    """Create one NSEC3 record line."""
    return f"{owner_hash}.{ORIGIN} 3600 IN NSEC3 1 {flags} {iterations} {salt} {next_hash} {' '.join(types)}"


def hash_is_covered(owner_hash, next_hash, value):
    """Return whether value is inside an NSEC3 circular interval."""
    if owner_hash == next_hash:
        return value != owner_hash
    if owner_hash < next_hash:
        return owner_hash < value <= next_hash
    return value > owner_hash or value <= next_hash


def zone_text(records, iterations=0, salt="-"):
    """Assemble a small offline zone."""
    base = [
        f"{ORIGIN} 3600 IN SOA ns.example. hostmaster.example. 1 7200 3600 1209600 3600",
        f"{ORIGIN} 3600 IN NS ns.example.",
        f"{ORIGIN} 3600 IN NSEC3PARAM 1 0 {iterations} {salt}",
    ]
    return "\n".join(base + records) + "\n"


def zone_text_for_origin(origin, records, iterations=0, salt="-"):
    """Assemble a zone whose SOA owner defines a non-default origin."""
    base = [
        f"{origin} 3600 IN SOA ns.{origin} hostmaster.{origin} 1 7200 3600 1209600 3600",
        f"{origin} 3600 IN NS ns.{origin}",
        f"{origin} 3600 IN NSEC3PARAM 1 0 {iterations} {salt}",
    ]
    return "\n".join(base + records) + "\n"


def zone_text_without_nsec3param(records):
    """Assemble a zone whose NSEC3 parameters must come from NSEC3 records."""
    base = [
        f"{ORIGIN} 3600 IN SOA ns.example. hostmaster.example. 1 7200 3600 1209600 3600",
        f"{ORIGIN} 3600 IN NS ns.example.",
    ]
    return "\n".join(base + records) + "\n"


@pytest.fixture(scope="session", autouse=True)
def built_binary():
    """Build the command from the submitted source once for all checks."""
    subprocess.run(["make", "-C", "/app", "clean", "all"], check=True)


def run_audit(tmp_path, text, query, qtype):
    """Run the audit command and parse its JSON response."""
    zone = tmp_path / "case.zone"
    zone.write_text(text)
    proc = subprocess.run(
        ["/app/nsec3audit", "--zone", str(zone), "--query", query, "--type", qtype],
        text=True,
        capture_output=True,
        check=True,
    )
    assert proc.stderr == ""
    return json.loads(proc.stdout)


def assert_shape(result, query, qtype):
    """Check the stable response schema shared by every result."""
    assert set(result) == {"qname", "qtype", "status", "owner", "proof"}
    assert result["qname"] == present(decode_name(query))
    assert result["qtype"] == qtype
    assert result["proof"] == sorted(result["proof"])
    assert all(set(item) <= set(ALPHA) for item in result["proof"])


def test_canonical_positive_answer(tmp_path):
    """Existing data is reported with canonical names and no denial proof."""
    text = zone_text([r"\065PI.Mixed 3600 IN A 192.0.2.10"])
    result = run_audit(tmp_path, text, r"api.mixed.example.", "A")
    assert_shape(result, r"\065pi.Mixed.Example.", "A")
    assert result == {
        "qname": "api.mixed.example.",
        "qtype": "A",
        "status": "present",
        "owner": "api.mixed.example.",
        "proof": [],
    }


def test_salted_nodata_uses_matching_nsec3(tmp_path):
    """A present name with a missing type is denied by its matching NSEC3 record."""
    salt = "A1B2"
    iterations = 2
    h = nsec3_hash("Mail.Example.", iterations, salt)
    text = zone_text([
        "Mail 3600 IN MX 10 mail.example.",
        nsec3(h, 0, iterations, salt, TOP, ["MX", "RRSIG"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, "mail.example.", "TXT")
    assert_shape(result, "mail.example.", "TXT")
    assert result["status"] == "nodata"
    assert result["owner"] == "mail.example."
    assert result["proof"] == [h]


def test_wildcard_answer_obeys_closest_encloser(tmp_path):
    """A wildcard at the closest existing ancestor can prove the selected owner."""
    text = zone_text([
        "Service 3600 IN TXT anchor",
        "*.Service 3600 IN A 192.0.2.55",
    ])
    result = run_audit(tmp_path, text, "Host.Service.Example.", "A")
    assert_shape(result, "host.service.example.", "A")
    assert result["status"] == "wildcard"
    assert result["owner"] == "*.service.example."
    assert result["proof"] == []


def test_denial_chain_is_canonical_and_ordered(tmp_path):
    """A generated absence proof requires the canonical NSEC3 chain and stable output ordering."""
    salt = "0F0E"
    iterations = 3
    text = zone_text([
        nsec3(ZERO, 0, iterations, salt, TOP, ["A", "NS", "SOA"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, r"Ghost.\068eep.Example.", "AAAA")
    assert_shape(result, r"ghost.\068eep.example.", "AAAA")
    assert result["status"] == "nxdomain"
    assert result["owner"] is None
    assert result["proof"] == [ZERO]


def test_distinct_nsec3_covers_are_both_reported(tmp_path):
    """Separate next-closer and wildcard cover records must both appear in the denial proof."""
    salt = "D15C"
    iterations = 2
    nc_hash = nsec3_hash("vault.example.", iterations, salt)
    wc_hash = nsec3_hash("*.example.", iterations, salt)
    nc_owner = predecessor_hash(nc_hash)
    wc_owner = predecessor_hash(wc_hash)
    assert nc_owner != wc_owner
    text = zone_text([
        nsec3(nc_owner, 0, iterations, salt, nc_hash, ["NS", "SOA"]),
        nsec3(wc_owner, 0, iterations, salt, wc_hash, ["NS", "SOA"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, "ghost.vault.example.", "AAAA")
    assert_shape(result, "ghost.vault.example.", "AAAA")
    assert result["status"] == "nxdomain"
    assert result["owner"] is None
    assert result["proof"] == sorted([nc_owner, wc_owner])


def test_nsec3_wraparound_interval_covers_absence(tmp_path):
    """A cover whose owner hash sorts after its next hash is still a valid circular interval."""
    salt = "A7A7"
    iterations = 1
    nc_hash = nsec3_hash("wrap.example.", iterations, salt)
    wc_hash = nsec3_hash("*.example.", iterations, salt)
    next_hash = max(nc_hash, wc_hash)
    owner_hash = successor_hash(next_hash)
    assert owner_hash > next_hash
    text = zone_text([
        nsec3(owner_hash, 0, iterations, salt, next_hash, ["NS", "SOA"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, "ghost.wrap.example.", "A")
    assert_shape(result, "ghost.wrap.example.", "A")
    assert result["status"] == "nxdomain"
    assert result["owner"] is None
    assert result["proof"] == [owner_hash]


def test_distinct_wraparound_covers_are_both_reported(tmp_path):
    """Distinct circular covers for next-closer and wildcard names must both appear."""
    salt = "8BAD"
    iterations = 2
    nc_hash = nsec3_hash("needle.ring.example.", iterations, salt)
    wc_hash = nsec3_hash("*.example.", iterations, salt)
    assert nc_hash != wc_hash

    low_hash, high_hash = sorted([nc_hash, wc_hash])
    low_owner = high_hash
    low_next = low_hash
    high_owner = predecessor_hash(high_hash)
    high_next = predecessor_hash(low_hash)
    assert low_owner > low_next
    assert high_owner > high_next
    assert hash_is_covered(low_owner, low_next, low_hash)
    assert not hash_is_covered(low_owner, low_next, high_hash)
    assert hash_is_covered(high_owner, high_next, high_hash)
    assert not hash_is_covered(high_owner, high_next, low_hash)

    if nc_hash == low_hash:
        nc_owner, nc_next = low_owner, low_next
        wc_owner, wc_next = high_owner, high_next
    else:
        nc_owner, nc_next = high_owner, high_next
        wc_owner, wc_next = low_owner, low_next

    text = zone_text([
        nsec3(nc_owner, 0, iterations, salt, nc_next, ["NS", "SOA"]),
        nsec3(wc_owner, 0, iterations, salt, wc_next, ["NS", "SOA"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, "shadow.needle.ring.example.", "A")
    assert_shape(result, "shadow.needle.ring.example.", "A")
    assert result["status"] == "nxdomain"
    assert result["owner"] is None
    assert result["proof"] == sorted([nc_owner, wc_owner])


def test_unsigned_delegation_is_not_secure_denial(tmp_path):
    """Names below an unsigned delegation are reported separately from secure absence."""
    text = zone_text([
        "Child 3600 IN NS ns.child.example.",
        nsec3(ZERO, 0, 1, "BEEF", TOP, ["NS", "SOA"]),
    ], 1, "BEEF")
    result = run_audit(tmp_path, text, "www.child.example.", "A")
    assert_shape(result, "www.child.example.", "A")
    assert result["status"] == "insecure_delegation"
    assert result["owner"] == "child.example."
    assert result["proof"] == []


def test_signed_delegation_uses_secure_denial(tmp_path):
    """A child delegation with a DS record remains in secure denial processing."""
    text = zone_text([
        "Child 3600 IN NS ns.child.example.",
        "Child 3600 IN DS 12345 8 2 AABBCCDDEEFF",
        nsec3(ZERO, 0, 1, "FACE", TOP, ["NS", "SOA"]),
    ], 1, "FACE")
    result = run_audit(tmp_path, text, "www.child.example.", "A")
    assert_shape(result, "www.child.example.", "A")
    assert result["status"] == "nxdomain"
    assert result["owner"] is None
    assert result["proof"] == [ZERO]


def test_opt_out_cover_changes_denial_class(tmp_path):
    """An opt-out cover is not reported as a secure NXDOMAIN proof."""
    text = zone_text([
        nsec3(ZERO, 1, 1, "C0DE", TOP, ["NS", "SOA"]),
    ], 1, "C0DE")
    result = run_audit(tmp_path, text, "www.unsigned.example.", "A")
    assert_shape(result, "www.unsigned.example.", "A")
    assert result["status"] == "insecure_delegation"
    assert result["owner"] is None
    assert result["proof"] == [ZERO]


def test_missing_nsec3_material_is_bogus(tmp_path):
    """A denial response without enough chain material is rejected."""
    salt = "CAFE"
    iterations = 1
    text = zone_text([
        nsec3(TOP, 0, iterations, salt, ZERO, ["A"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, "absent.example.", "A")
    assert_shape(result, "absent.example.", "A")
    assert result["status"] == "bogus"
    assert result["owner"] is None
    assert result["proof"] == []


def test_origin_relative_query_uses_nsec3_record_parameters(tmp_path):
    """Origin-relative names still validate when parameters come only from NSEC3 records."""
    salt = "1234"
    iterations = 1
    h = nsec3_hash("Alias.Example.", iterations, salt)
    text = zone_text_without_nsec3param([
        "Alias 3600 IN A 192.0.2.44",
        nsec3(h, 0, iterations, salt, TOP, ["A", "RRSIG"]),
    ])
    result = run_audit(tmp_path, text, "ALIAS", "AAAA")
    assert_shape(result, "alias.example.", "AAAA")
    assert result["status"] == "nodata"
    assert result["owner"] == "alias.example."
    assert result["proof"] == [h]


def test_generated_zone_variants_require_real_nsec3_logic(tmp_path):
    """Generated labels and salts require parsing, hashing, and denial proof logic."""
    variants = [
        ("A11C", 1, "amber", "MX", "TXT"),
        ("5EED", 2, "cobalt", "TXT", "AAAA"),
    ]

    for index, (salt, iterations, stem, present_type, missing_type) in enumerate(variants):
        escaped_owner = rf"\065lt{index}.{stem}"
        canonical_owner = f"alt{index}.{stem}.example."
        exact_hash = nsec3_hash(canonical_owner, iterations, salt)
        text = zone_text([
            f"{escaped_owner} 3600 IN {present_type} token-{index}",
            nsec3(exact_hash, 0, iterations, salt, TOP, [present_type, "RRSIG"]),
        ], iterations, salt)
        result = run_audit(tmp_path, text, canonical_owner.upper(), missing_type)
        assert_shape(result, canonical_owner.upper(), missing_type)
        assert result["status"] == "nodata"
        assert result["owner"] == canonical_owner
        assert result["proof"] == [exact_hash]

        query = f"ghost{index}.gap{index}.{stem}.example."
        next_closer = f"gap{index}.{stem}.example."
        wildcard = f"*.{stem}.example."
        nc_hash = nsec3_hash(next_closer, iterations, salt)
        wc_hash = nsec3_hash(wildcard, iterations, salt)
        assert nc_hash != wc_hash
        nc_owner = predecessor_hash(nc_hash)
        wc_owner = predecessor_hash(wc_hash)
        assert nc_owner != wc_owner
        text = zone_text([
            f"{stem} 3600 IN TXT closest-encloser-{index}",
            nsec3(nc_owner, 0, iterations, salt, nc_hash, ["NS", "SOA"]),
            nsec3(wc_owner, 0, iterations, salt, wc_hash, ["NS", "SOA"]),
        ], iterations, salt)
        result = run_audit(tmp_path, text, query, "A")
        assert_shape(result, query, "A")
        assert result["status"] == "nxdomain"
        assert result["owner"] is None
        assert result["proof"] == sorted([nc_owner, wc_owner])


def test_nsec3param_selection_rejects_decoy_chain(tmp_path):
    """NSEC3PARAM controls which chain authenticates denial when decoys coexist."""
    good_salt = "C1A0"
    good_iterations = 2
    bad_salt = "BEEF"
    bad_iterations = 1
    qname = "missing.branch.example."
    next_closer = "branch.example."
    wildcard = "*.example."

    good_nc_hash = nsec3_hash(next_closer, good_iterations, good_salt)
    good_wc_hash = nsec3_hash(wildcard, good_iterations, good_salt)
    good_nc_owner = predecessor_hash(good_nc_hash)
    good_wc_owner = predecessor_hash(good_wc_hash)

    decoy_nc_hash = nsec3_hash(next_closer, bad_iterations, bad_salt)
    decoy_owner = predecessor_hash(decoy_nc_hash)
    text = zone_text([
        nsec3(decoy_owner, 0, bad_iterations, bad_salt, decoy_nc_hash, ["NS", "SOA"]),
        nsec3(good_wc_owner, 0, good_iterations, good_salt, good_wc_hash, ["NS", "SOA"]),
        nsec3(good_nc_owner, 0, good_iterations, good_salt, good_nc_hash, ["NS", "SOA"]),
    ], good_iterations, good_salt)
    result = run_audit(tmp_path, text, qname, "AAAA")
    assert_shape(result, qname, "AAAA")
    assert result["status"] == "nxdomain"
    assert result["owner"] is None
    assert result["proof"] == sorted([good_nc_owner, good_wc_owner])


def test_nsec3_full_ring_cover_does_not_mask_exact_owner_gap(tmp_path):
    """A full-ring NSEC3 cover can deny absence but cannot replace an exact NODATA proof."""
    salt = "DEAD"
    iterations = 1
    owner = "exists.example."
    exact_hash = nsec3_hash(owner, iterations, salt)
    full_ring = predecessor_hash(exact_hash)
    text = zone_text([
        "Exists 3600 IN MX 10 mail.example.",
        nsec3(full_ring, 0, iterations, salt, full_ring, ["NS", "SOA"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, owner, "TXT")
    assert_shape(result, owner, "TXT")
    assert result["status"] == "bogus"
    assert result["owner"] is None
    assert result["proof"] == []


def test_unsigned_delegation_takes_precedence_over_wildcard_and_optout(tmp_path):
    """An unsigned cut dominates wildcard data and opt-out denial below that cut."""
    salt = "0BAD"
    iterations = 2
    qname = "api.child.example."
    q_hash = nsec3_hash(qname, iterations, salt)
    cover_owner = predecessor_hash(q_hash)
    text = zone_text([
        "Child 3600 IN NS ns.child.example.",
        "*.Child 3600 IN A 192.0.2.77",
        nsec3(cover_owner, 1, iterations, salt, q_hash, ["NS", "SOA"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, qname, "A")
    assert_shape(result, qname, "A")
    assert result["status"] == "insecure_delegation"
    assert result["owner"] == "child.example."
    assert result["proof"] == []


def test_soa_owner_defines_non_default_origin(tmp_path):
    """Relative names and hashes follow the SOA owner rather than a built-in zone name."""
    origin = "secure.test."
    salt = "44AA"
    iterations = 1
    owner = f"node.{origin}"
    h = nsec3_hash(owner, iterations, salt, origin)
    text = zone_text_for_origin(origin, [
        "Node 3600 IN TXT token",
        nsec3(h, 0, iterations, salt, TOP, ["TXT", "RRSIG"]),
    ], iterations, salt)
    result = run_audit(tmp_path, text, "NODE", "AAAA")
    assert_shape(result, owner, "AAAA")
    assert result["status"] == "nodata"
    assert result["owner"] == owner
    assert result["proof"] == [h]
