import base64
import struct
import subprocess
import tempfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

CERT_ALG = b"ssh-ed25519-cert-v01@openssh.com"
PLAIN_ALG = b"ssh-ed25519"
CA = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
USER = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
OTHER = Ed25519PrivateKey.from_private_bytes(bytes(range(65, 97)))


def u32(n):
    return struct.pack(">I", n)


def u64(n):
    return struct.pack(">Q", n)


def sshstr(value):
    return u32(len(value)) + value


def public(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def sigblob(key, data):
    return sshstr(PLAIN_ALG) + sshstr(key.sign(data))


def option_blob(entries):
    return b"".join(sshstr(name) + sshstr(sshstr(value)) for name, value in entries)


def certificate(*, signer=CA, principals=(b"ops",), after=1000, before=2000,
                options=(), cert_type=1, serial=41):
    ca_blob = sshstr(PLAIN_ALG) + sshstr(public(signer))
    principal_blob = b"".join(sshstr(p) for p in principals)
    body = (
        sshstr(CERT_ALG)
        + sshstr(b"offline-nonce")
        + sshstr(public(USER))
        + u64(serial)
        + u32(cert_type)
        + sshstr(b"capture-key")
        + sshstr(principal_blob)
        + u64(after)
        + u64(before)
        + sshstr(option_blob(options))
        + sshstr(b"")
        + sshstr(b"")
        + sshstr(ca_blob)
    )
    return body + sshstr(sigblob(signer, body))


def kex(kex_names, host_names):
    empty = sshstr(b"")
    return (
        b"\x14" + bytes(range(16)) + sshstr(b",".join(kex_names))
        + sshstr(b",".join(host_names)) + empty * 8 + b"\x00" + u32(0)
    )


def ext_info(algorithms):
    return b"\x07" + u32(1) + sshstr(b"server-sig-algs") + sshstr(b",".join(algorithms))


def auth(session, cert, signer=USER, principal=b"ops"):
    unsigned = (
        b"\x32" + sshstr(principal) + sshstr(b"ssh-connection")
        + sshstr(b"publickey") + b"\x01" + sshstr(CERT_ALG) + sshstr(cert)
    )
    signature = sigblob(signer, sshstr(session) + unsigned)
    return unsigned + sshstr(signature)


def packet(payload):
    padding = 4
    while 4 + 1 + len(payload) + padding < 16 or (4 + 1 + len(payload) + padding) % 8:
        padding += 1
    length = 1 + len(payload) + padding
    return u32(length) + bytes([padding]) + payload + bytes([0xA5]) * padding


def transcript(cert, *, auth_signer=USER, server_algs=(CERT_ALG,), fragments=None,
               second_epoch=None, second_epoch_ext=True, trailing=b"", principal=b"ops",
               client_kex=None, server_kex=None):
    session = b"fixed-session-identifier"
    client_kex = client_kex or [b"curve25519-sha256", b"ext-info-c"]
    server_kex = server_kex or [b"curve25519-sha256", b"ext-info-s"]
    host = [b"ssh-ed25519"]
    events = [
        (0, packet(kex(client_kex, host))),
        (1, packet(kex(server_kex, host))),
        (0, packet(b"\x15")),
        (1, packet(b"\x15")),
        (1, packet(ext_info(server_algs))),
    ]
    if second_epoch is not None:
        events.extend([
            (0, packet(kex(client_kex, host))),
            (1, packet(kex(server_kex, host))),
            (0, packet(b"\x15")),
            (1, packet(b"\x15")),
        ])
        if second_epoch_ext:
            events.append((1, packet(ext_info(second_epoch))))
    events.append((0, packet(auth(session, cert, auth_signer, principal))))
    if trailing:
        events.append((0, trailing))
    records = bytearray()
    for direction, raw in events:
        sizes = fragments(raw) if fragments else [len(raw)]
        at = 0
        for size in sizes:
            part = raw[at:at + size]
            at += size
            records.extend(bytes([direction]) + struct.pack(">H", len(part)) + part)
        assert at == len(raw)
    return b"SSHC" + sshstr(session) + bytes(records)


def transcript_without_newkeys(cert):
    session = b"fixed-session-identifier"
    host = [b"ssh-ed25519"]
    events = [
        (0, packet(kex([b"curve25519-sha256", b"ext-info-c"], host))),
        (1, packet(kex([b"curve25519-sha256", b"ext-info-s"], host))),
        (1, packet(ext_info((CERT_ALG,)))),
        (0, packet(auth(session, cert))),
    ]
    records = bytearray()
    for direction, raw in events:
        records.extend(bytes([direction]) + struct.pack(">H", len(raw)) + raw)
    return b"SSHC" + sshstr(session) + bytes(records)


def run_case(capture, *, now=1500, principal="ops", address="10.4.8.9", command="deploy"):
    ca_blob = sshstr(PLAIN_ALG) + sshstr(public(CA))
    line = PLAIN_ALG + b" " + base64.b64encode(ca_blob) + b" policy-ca\n"
    with tempfile.NamedTemporaryFile() as handle:
        handle.write(capture)
        handle.flush()
        with open("/app/trusted_ca.pub", "wb") as trust:
            trust.write(line)
        result = subprocess.run(
            ["/app/ssh-policy", handle.name, str(now), principal, address, command],
            text=True, capture_output=True, timeout=8, check=True,
        )
    assert result.stderr == ""
    assert result.stdout in {"ACCEPT\n", "REJECT\n"}
    return result.stdout.strip()


def split_pattern(raw):
    """Produce deterministic record boundaries independent of SSH packet boundaries."""
    sizes = []
    left = len(raw)
    step = 1
    while left:
        take = min(left, (step * 7) % 19 + 1)
        sizes.append(take)
        left -= take
        step += 1
    return sizes


def test_policy_matrix_a():
    """A conforming certificate is accepted only under its requested restrictions."""
    cert = certificate(options=((b"force-command", b"deploy"),
                                (b"source-address", b"10.4.8.0/24,192.0.2.1/32")))
    capture = transcript(cert)
    assert run_case(capture) == "ACCEPT"
    assert run_case(capture, address="10.4.9.9") == "REJECT"
    assert run_case(capture, command="shell") == "REJECT"
    assert run_case(capture, principal="operator") == "REJECT"


def test_policy_matrix_b():
    """Certificate and request authentication are independently enforced."""
    assert run_case(transcript(certificate(signer=OTHER))) == "REJECT"
    assert run_case(transcript(certificate(), auth_signer=OTHER)) == "REJECT"
    assert run_case(transcript(certificate(cert_type=2))) == "REJECT"


def test_policy_matrix_c():
    """Validity uses the certificate interval's defined endpoint semantics."""
    cert = certificate(after=1200, before=1600)
    assert run_case(transcript(cert), now=1200) == "ACCEPT"
    assert run_case(transcript(cert), now=1599) == "ACCEPT"
    assert run_case(transcript(cert), now=1600) == "REJECT"


@pytest.mark.parametrize("entries", [
    pytest.param(((b"permit-pty", b""),), id="unknown-critical-option"),
    pytest.param(((b"source-address", b"10.0.0.0/8"),
                  (b"source-address", b"10.4.0.0/16")),
                 id="duplicate-critical-option"),
    pytest.param(((b"source-address", b"10.4.0.0/16"),
                  (b"force-command", b"deploy")),
                 id="wrong-lexicographic-order"),
])
def test_policy_matrix_d(entries):
    """Unknown, duplicate, and out-of-order critical options are each rejected."""
    assert run_case(transcript(certificate(options=entries))) == "REJECT"


def test_policy_matrix_e():
    """Capture record segmentation does not alter packet-stream meaning."""
    cert = certificate()
    assert run_case(transcript(cert, fragments=split_pattern)) == "ACCEPT"
    assert run_case(transcript(cert, fragments=split_pattern, trailing=b"\x00\x00")) == "REJECT"


def test_policy_matrix_e_rejects_bad_record_framing():
    """Malformed record lengths are rejected rather than silently ignored."""
    cert = certificate()
    capture = transcript(cert)
    assert run_case(capture) == "ACCEPT"
    assert run_case(capture + b"\x00\x00\x05\x99") == "REJECT"


def test_policy_matrix_f():
    """Authorization is bound to the negotiated properties of the current key epoch."""
    cert = certificate()
    assert run_case(transcript(cert, second_epoch=(b"ssh-ed25519",))) == "REJECT"
    assert run_case(transcript(cert, second_epoch=(), second_epoch_ext=False)) == "REJECT"
    assert run_case(transcript(cert, second_epoch=(CERT_ALG,))) == "ACCEPT"


def test_policy_matrix_f_requires_completed_newkeys():
    """User authentication is rejected until both sides complete NEWKEYS."""
    assert run_case(transcript_without_newkeys(certificate())) == "REJECT"


def test_policy_matrix_g():
    """Principal comparison is exact and does not admit related account names."""
    cert = certificate(principals=(b"ops", b"audit"))
    assert run_case(transcript(cert, principal=b"ops"), principal="ops") == "ACCEPT"
    assert run_case(transcript(cert, principal=b"ops-admin"), principal="ops-admin") == "REJECT"


def test_policy_matrix_h():
    """Extension and algorithm use requires a valid two-party negotiation."""
    cert = certificate()
    assert run_case(transcript(cert, client_kex=[b"curve25519-sha256"])) == "REJECT"
    assert run_case(transcript(
        cert,
        client_kex=[b"diffie-hellman-group14-sha256", b"ext-info-c"],
        server_kex=[b"curve25519-sha256", b"ext-info-s"],
    )) == "REJECT"
