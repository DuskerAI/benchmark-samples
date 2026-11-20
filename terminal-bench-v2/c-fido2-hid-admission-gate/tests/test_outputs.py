import json
import subprocess
from pathlib import Path

ROOT = Path("/app/fidogate")


def _run_gate(
    tmp_path: Path,
    policy: str,
    state: str,
    records_by_file: dict[str, list[tuple[int, str, bytes]]],
):
    subprocess.run([str(ROOT / "scripts/build.sh")], cwd=ROOT, check=True)
    case = tmp_path / "case"
    captures = case / "captures"
    captures.mkdir(parents=True)
    for name, rows in records_by_file.items():
        with (captures / name).open("w", encoding="utf-8") as f:
            for arrival, cid, frame in rows:
                f.write(f"{arrival} {cid} {frame.hex()}\n")
    policy_path = case / "policy.txt"
    policy_path.write_text(policy, encoding="utf-8")
    state_path = case / "state.txt"
    state_path.write_text(state, encoding="utf-8")
    out = case / "nested" / "report.json"
    result = subprocess.run(
        [
            str(ROOT / "bin/fidogate"),
            "--policy",
            str(policy_path),
            "--captures",
            str(captures),
            "--state",
            str(state_path),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert sorted(p.name for p in out.parent.iterdir()) == ["report.json"]
    return json.loads(out.read_text(encoding="utf-8")), captures


def _run_gate_text(tmp_path: Path, policy: str, state: str, files: dict[str, str]):
    subprocess.run([str(ROOT / "scripts/build.sh")], cwd=ROOT, check=True)
    case = tmp_path / "case"
    captures = case / "captures"
    captures.mkdir(parents=True)
    for name, text in files.items():
        (captures / name).write_text(text, encoding="utf-8")
    policy_path = case / "policy.txt"
    policy_path.write_text(policy, encoding="utf-8")
    state_path = case / "state.txt"
    state_path.write_text(state, encoding="utf-8")
    out = case / "nested" / "report.json"
    result = subprocess.run(
        [
            str(ROOT / "bin/fidogate"),
            "--policy",
            str(policy_path),
            "--captures",
            str(captures),
            "--state",
            str(state_path),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert sorted(p.name for p in out.parent.iterdir()) == ["report.json"]
    return json.loads(out.read_text(encoding="utf-8")), captures


def _uint(n: int) -> bytes:
    if n < 24:
        return bytes([n])
    if n < 256:
        return bytes([0x18, n])
    if n < 65536:
        return bytes([0x19, n >> 8, n & 0xFF])
    return bytes([0x1A, (n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255, n & 255])


def _bstr(raw: bytes) -> bytes:
    return bytes([0x40 + len(raw)]) + raw


def _text(s: str) -> bytes:
    raw = s.encode()
    return bytes([0x60 + len(raw)]) + raw


def _bool(v: bool) -> bytes:
    return b"\xf5" if v else b"\xf4"


def _emap(values: dict[str, object]) -> bytes:
    def enc(v: object) -> bytes:
        if isinstance(v, bool):
            return _bool(v)
        if isinstance(v, bytes):
            return _bstr(v)
        if isinstance(v, tuple) and len(v) == 2 and v[0] == "text":
            return _text(str(v[1]))
        return _uint(int(v))

    pairs = [
        (_text(k), enc(v))
        for k, v in values.items()
    ]
    pairs.sort(key=lambda kv: kv[0])
    return bytes([0xA0 + len(pairs)]) + b"".join(k + v for k, v in pairs)


def _raw_assertion(
    eid: str,
    auth: str = "auth-a",
    cred_hex: str = "aabbccdd",
    count: int = 10,
    flags: int = 0x81,
    challenge_hex: str = "00112233445566778899aabbccddeeff",
    rp: str = "login.example",
    ext_bytes: bytes | None = None,
    count_bytes: bytes | None = None,
    status: int = 0,
) -> bytes:
    fields = [
        _uint(0) + _text(eid),
        _uint(1) + _text(auth),
        _uint(2) + _bstr(bytes.fromhex(cred_hex)),
        _uint(3) + (count_bytes if count_bytes is not None else _uint(count)),
        _uint(4) + _uint(flags),
        _uint(5) + _bstr(bytes.fromhex(challenge_hex)),
        _uint(6) + _text(rp),
        _uint(7) + (ext_bytes if ext_bytes is not None else _emap({"credProtect": 2})),
    ]
    return bytes([status]) + bytes([0xA8]) + b"".join(fields)


def _custom_assertion(eid: str, overrides: dict[int, bytes]) -> bytes:
    fields = {
        0: _text(eid),
        1: _text("auth-a"),
        2: _bstr(bytes.fromhex("aabbccdd")),
        3: _uint(10),
        4: _uint(0x81),
        5: _bstr(bytes.fromhex("00112233445566778899aabbccddeeff")),
        6: _text("login.example"),
        7: _emap({"credProtect": 2}),
    }
    fields.update(overrides)
    return b"\x00" + bytes([0xA8]) + b"".join(_uint(k) + fields[k] for k in range(8))


def _assertion(
    eid: str,
    auth: str,
    cred_hex: str,
    count: int,
    flags: int,
    challenge_hex: str,
    rp: str,
    exts: dict[str, object] | None = None,
) -> bytes:
    fields = [
        _uint(0) + _text(eid),
        _uint(1) + _text(auth),
        _uint(2) + _bstr(bytes.fromhex(cred_hex)),
        _uint(3) + _uint(count),
        _uint(4) + _uint(flags),
        _uint(5) + _bstr(bytes.fromhex(challenge_hex)),
        _uint(6) + _text(rp),
        _uint(7) + _emap(exts or {}),
    ]
    return b"\x00" + bytes([0xA8]) + b"".join(fields)


def _hid(cid: str, payload: bytes, chunk_sizes: list[int] | None = None) -> list[bytes]:
    c = bytes.fromhex(cid)
    size = len(payload)
    init_data_len = 57 if chunk_sizes is None else chunk_sizes[0]
    frames = [c + b"\x90" + bytes([size >> 8, size & 255]) + payload[:init_data_len]]
    pos = init_data_len
    seq = 0
    while pos < size:
        take = (
            59
            if chunk_sizes is None or seq + 1 >= len(chunk_sizes)
            else chunk_sizes[seq + 1]
        )
        frames.append(c + bytes([seq]) + payload[pos : pos + take])
        pos += take
        seq += 1
    return frames


def _message_rows(start: int, cid: str, payload: bytes) -> list[tuple[int, str, bytes]]:
    return [(start + i, cid, frame) for i, frame in enumerate(_hid(cid, payload))]


def _rows_text(rows: list[tuple[int, str, bytes]]) -> str:
    return "".join(f"{arrival} {cid} {frame.hex()}\n" for arrival, cid, frame in rows)


def _init_frame(cid: str, command: int, payload: bytes = b"") -> bytes:
    size = len(payload)
    return bytes.fromhex(cid) + bytes([command, size >> 8, size & 255]) + payload


BASE_POLICY = """# local admission policy\n\nrp_id=login.example\nchallenge=00112233445566778899aabbccddeeff\nallow_credential=aabbccdd\nallow_credential=01020304\nallow_authenticator=auth-a\nallow_authenticator=auth-b\nallow_extension=hmac-secret\nrequire_extension=credProtect=2\n"""


def test_reassembles_interleaved_hid_channels_and_orders_completed_evidence(tmp_path):
    """The verifier requires channel-aware HID reassembly before policy decisions."""
    msg_a = _assertion(
        "alpha",
        "auth-a",
        "aabbccdd",
        8,
        0x81,
        "00112233445566778899aabbccddeeff",
        "login.example",
        {"credProtect": 2, "hmac-secret": True},
    )
    msg_b = _assertion(
        "bravo",
        "auth-b",
        "01020304",
        4,
        0x81,
        "00112233445566778899aabbccddeeff",
        "login.example",
        {"credProtect": 2},
    )
    fa = _hid("01020304", msg_a, [9, 7, 999])
    fb = _hid("0a0b0c0d", msg_b, [10, 999])
    report, _ = _run_gate(
        tmp_path,
        BASE_POLICY,
        "auth-a aabbccdd 7\nauth-b 01020304 1\n",
        {
            "b.cap": [(1, "01020304", fa[0]), (3, "0a0b0c0d", fb[1])],
            "a.cap": [
                (2, "0a0b0c0d", fb[0]),
                (4, "01020304", fa[1]),
                (5, "01020304", fa[2]),
            ],
        },
    )
    assert [row["id"] for row in report["accepted"]] == ["bravo", "alpha"]
    assert report["summary"] == {"accepted": 2, "rejected": 0, "malformed": 0}


def test_enforces_canonical_cbor_shape_and_recovers_after_bad_records(tmp_path):
    """Malformed CBOR and unusable HID records must be rejected without aborting later evidence."""
    good = _assertion(
        "after",
        "auth-a",
        "aabbccdd",
        9,
        0x81,
        "00112233445566778899aabbccddeeff",
        "login.example",
        {"credProtect": 2},
    )
    duplicate_key_payload = b"\x00" + bytes.fromhex(
        "a90063647570016461200244aabbccdd031804040181050101066d6c6f67696e2e6578616d706c6507a16b6372656450726f74656374020163787570"
    )
    frames_bad = _hid("11111111", duplicate_key_payload)
    good_rows = _message_rows(3, "22222222", good)
    report, _ = _run_gate(
        tmp_path,
        BASE_POLICY,
        "auth-a aabbccdd 3\n",
        {
            "audit.cap": [
                (1, "11111111", frames_bad[0]),
                (2, "99999999", b"\x99bad"),
                *good_rows,
            ]
        },
    )
    assert report["accepted"][0]["id"] == "after"
    assert [r["reason"] for r in report["rejected"]] == ["malformed", "malformed"]
    assert report["summary"] == {"accepted": 1, "rejected": 2, "malformed": 2}


def test_capture_file_syntax_ordering_and_recoverable_hid_errors(tmp_path):
    """Capture syntax, filename filtering, tie ordering, and recoverable HID errors are observable behavior."""
    good = _assertion(
        "upper",
        "auth-a",
        "aabbccdd",
        14,
        0x81,
        "00112233445566778899aabbccddeeff",
        "login.example",
        {"credProtect": 2},
    )
    wrong_seq = _hid("12121212", good, [8, 999])
    accepted_rows = _message_rows(30, "ABCDEF01", good)
    wrong_continuation = bytes.fromhex("12121212") + b"\x01\xaa"
    files = {
        "z.cap": "# ignored\n5 zzzzzzzz gh\n"
        f"12 12121212 {wrong_seq[0].hex()}\n13 12121212 {wrong_continuation.hex()}\n",
        "a.cap": "\n5 aaaaaaaa zz\n"
        f"14 aaaaaaaa {_init_frame('bbbbbbbb', 0x90, good).hex()}\n15 cccccccc {_init_frame('cccccccc', 0x91, good).hex()}\n",
        "ignored.txt": _rows_text(_message_rows(1, "99999999", good)),
        "m.cap": _rows_text(accepted_rows),
    }
    report, _ = _run_gate_text(tmp_path, BASE_POLICY, "auth-a aabbccdd 1\n", files)
    assert report["accepted"] == [
        {
            "id": "upper",
            "authenticator": "auth-a",
            "credential": "aabbccdd",
            "sign_count": 14,
            "rp_id": "login.example",
        }
    ]
    assert [(row["id"], row["reason"]) for row in report["rejected"]] == [
        ("a.cap:2", "malformed"),
        ("z.cap:2", "malformed"),
        ("z.cap:3", "malformed"),
        ("a.cap:3", "malformed"),
        ("a.cap:4", "malformed"),
    ]
    assert report["summary"] == {"accepted": 1, "rejected": 5, "malformed": 5}


def test_eof_truncated_messages_reject_without_blocking_later_channels(tmp_path):
    """An unfinished CTAPHID message at EOF is malformed and does not block completed channels."""
    truncated = _hid(
        "51515151",
        _assertion(
            "unfinished",
            "auth-a",
            "aabbccdd",
            20,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
        [8, 999],
    )[0]
    good_rows = _message_rows(
        3,
        "61616161",
        _assertion(
            "complete",
            "auth-a",
            "aabbccdd",
            7,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
    )
    report, _ = _run_gate(
        tmp_path,
        BASE_POLICY,
        "auth-a aabbccdd 6\n",
        {"trunc.cap": [(1, "51515151", truncated), *good_rows]},
    )
    assert report["accepted"] == [
        {
            "id": "complete",
            "authenticator": "auth-a",
            "credential": "aabbccdd",
            "sign_count": 7,
            "rp_id": "login.example",
        }
    ]
    assert report["rejected"] == [{"id": "trunc.cap:1", "reason": "malformed"}]
    assert report["summary"] == {"accepted": 1, "rejected": 1, "malformed": 1}


def test_cbor_canonical_flags_and_extension_shape_are_enforced(tmp_path):
    """CBOR canonical encoding, assertion flags, and extension-map shape are rejected before admission."""
    unsorted_ext = b"\xa2" + _text("z") + _uint(1) + _text("a") + _uint(1)
    indefinite_text = b"\x7f" + _text("login") + _text(".example") + b"\xff"
    indefinite_array_value = b"\xa1" + _text("credProtect") + b"\x9f\x02\xff"
    rows: list[tuple[int, str, bytes]] = []
    cases = [
        _raw_assertion("bad-status", status=0x2E),
        _raw_assertion("wide-count", count_bytes=b"\x18\x05"),
        _raw_assertion("no-up", flags=0x80),
        _raw_assertion("flag-empty", flags=0x81, ext_bytes=_emap({})),
        _raw_assertion("flag-missing", flags=0x01, ext_bytes=_emap({"credProtect": 2})),
        _raw_assertion("unsorted-ext", ext_bytes=unsorted_ext),
        _custom_assertion("indef-text", {6: indefinite_text}),
        _custom_assertion("indef-array", {7: indefinite_array_value}),
        _raw_assertion("indef-map", ext_bytes=b"\xbf\xff"),
    ]
    arrival = 1
    for payload in cases:
        message_rows = _message_rows(arrival, "56565656", payload)
        rows.extend(message_rows)
        arrival += len(message_rows)
    report, _ = _run_gate(
        tmp_path, BASE_POLICY, "auth-a aabbccdd 0\n", {"cbor.cap": rows}
    )
    assert [(row["id"], row["reason"]) for row in report["rejected"]] == [
        ("cbor.cap:1", "malformed"),
        ("wide-count", "malformed"),
        ("no-up", "malformed"),
        ("flag-empty", "malformed"),
        ("flag-missing", "malformed"),
        ("unsorted-ext", "malformed"),
        ("indef-text", "malformed"),
        ("indef-array", "malformed"),
        ("indef-map", "malformed"),
    ]
    assert report["accepted"] == []


def test_required_extension_false_is_a_real_policy_value(tmp_path):
    """A required boolean false extension is accepted only when the assertion carries false."""
    policy = """rp_id=login.example\nchallenge=00112233445566778899aabbccddeeff\nallow_credential=aabbccdd\nallow_authenticator=auth-a\nrequire_extension=uv=false\n"""
    rows: list[tuple[int, str, bytes]] = []
    arrival = 1
    for payload in [
        _assertion(
            "false-ok",
            "auth-a",
            "aabbccdd",
            1,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"uv": False},
        ),
        _assertion(
            "true-bad",
            "auth-a",
            "aabbccdd",
            2,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"uv": True},
        ),
        _assertion(
            "missing-bad",
            "auth-a",
            "aabbccdd",
            3,
            0x01,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {},
        ),
    ]:
        message_rows = _message_rows(arrival, "77777777", payload)
        rows.extend(message_rows)
        arrival += len(message_rows)
    report, _ = _run_gate(tmp_path, policy, "auth-a aabbccdd 0\n", {"false.cap": rows})
    assert report["accepted"] == [
        {
            "id": "false-ok",
            "authenticator": "auth-a",
            "credential": "aabbccdd",
            "sign_count": 1,
            "rp_id": "login.example",
        }
    ]
    assert [(row["id"], row["reason"]) for row in report["rejected"]] == [
        ("true-bad", "extension"),
        ("missing-bad", "extension"),
    ]


def test_typed_extension_policy_values_keep_byte_and_text_values_distinct(tmp_path):
    """Required extensions compare CBOR value type as well as the value bytes."""
    policy = """rp_id=login.example
challenge=00112233445566778899aabbccddeeff
allow_credential=aabbccdd
allow_authenticator=auth-a
allow_extension=hmac-secret
require_extension=device=hex:0102030405060708
require_extension=profile=text:managed
"""
    rows: list[tuple[int, str, bytes]] = []
    arrival = 1
    for payload in [
        _assertion(
            "typed-ok",
            "auth-a",
            "aabbccdd",
            31,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"device": bytes.fromhex("0102030405060708"), "profile": ("text", "managed")},
        ),
        _assertion(
            "device-as-text",
            "auth-a",
            "aabbccdd",
            32,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"device": ("text", "0102030405060708"), "profile": ("text", "managed")},
        ),
        _assertion(
            "profile-as-bytes",
            "auth-a",
            "aabbccdd",
            33,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"device": bytes.fromhex("0102030405060708"), "profile": b"managed"},
        ),
        _assertion(
            "unlisted-extra",
            "auth-a",
            "aabbccdd",
            34,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"device": bytes.fromhex("0102030405060708"), "profile": ("text", "managed"), "uvm": 1},
        ),
    ]:
        message_rows = _message_rows(arrival, "2468ace0", payload)
        rows.extend(message_rows)
        arrival += len(message_rows)
    report, _ = _run_gate(tmp_path, policy, "auth-a aabbccdd 30\n", {"typed.cap": rows})
    assert [r["id"] for r in report["accepted"]] == ["typed-ok"]
    assert [(r["id"], r["reason"]) for r in report["rejected"]] == [
        ("device-as-text", "extension"),
        ("profile-as-bytes", "extension"),
        ("unlisted-extra", "extension"),
    ]
    assert report["summary"] == {"accepted": 1, "rejected": 3, "malformed": 0}


def test_applies_policy_extensions_and_stateful_replay_rules(tmp_path):
    """Policy allowlists, required extension values, and per-credential counters are independent admission controls."""
    rows = []
    arrival = 1
    for payload in [
        _assertion(
            "old",
            "auth-a",
            "aabbccdd",
            5,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
        _assertion(
            "missing-ext",
            "auth-a",
            "aabbccdd",
            100,
            0x01,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {},
        ),
        _assertion(
            "wrong-ext",
            "auth-a",
            "aabbccdd",
            101,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 1},
        ),
        _assertion(
            "extra-ext",
            "auth-a",
            "aabbccdd",
            102,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2, "uvm": True},
        ),
        _assertion(
            "bad-rp",
            "auth-a",
            "aabbccdd",
            103,
            0x81,
            "00112233445566778899aabbccddeeff",
            "evil.example",
            {"credProtect": 2},
        ),
        _assertion(
            "bad-challenge",
            "auth-a",
            "aabbccdd",
            104,
            0x81,
            "ffeeddccbbaa99887766554433221100",
            "login.example",
            {"credProtect": 2},
        ),
        _assertion(
            "bad-credential",
            "auth-a",
            "deadbeef",
            105,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
        _assertion(
            "bad-auth",
            "auth-x",
            "aabbccdd",
            106,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
        _raw_assertion("bad-cbor", count=107, ext_bytes=b"\xbf\xff"),
        _assertion(
            "fresh",
            "auth-a",
            "aabbccdd",
            7,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
        _assertion(
            "repeat",
            "auth-a",
            "aabbccdd",
            7,
            0x81,
            "00112233445566778899aabbccddeeff",
            "login.example",
            {"credProtect": 2},
        ),
    ]:
        message_rows = _message_rows(arrival, "33333333", payload)
        rows.extend(message_rows)
        arrival += len(message_rows)
    report, _ = _run_gate(
        tmp_path, BASE_POLICY, "auth-a aabbccdd 6\n", {"state.cap": rows}
    )
    assert [r["id"] for r in report["accepted"]] == ["fresh"]
    assert [(r["id"], r["reason"]) for r in report["rejected"]] == [
        ("old", "replay"),
        ("missing-ext", "extension"),
        ("wrong-ext", "extension"),
        ("extra-ext", "extension"),
        ("bad-rp", "policy"),
        ("bad-challenge", "policy"),
        ("bad-credential", "policy"),
        ("bad-auth", "policy"),
        ("bad-cbor", "malformed"),
        ("repeat", "replay"),
    ]
    assert report["summary"] == {"accepted": 1, "rejected": 10, "malformed": 1}


def test_report_schema_is_deterministic_and_inputs_are_preserved(tmp_path):
    """The report has the public schema only, stable ordering, and no capture mutation."""
    msg = _assertion(
        "stable",
        "auth-b",
        "01020304",
        12,
        0x81,
        "00112233445566778899aabbccddeeff",
        "login.example",
        {"credProtect": 2},
    )
    rows = _message_rows(42, "44444444", msg)
    report, captures = _run_gate(
        tmp_path / "one",
        BASE_POLICY,
        "# saved counters\n\nauth-b 01020304 11\n",
        {"stable.cap": rows},
    )
    before = (captures / "stable.cap").read_bytes()
    report2, _ = _run_gate(
        tmp_path / "two",
        BASE_POLICY,
        "# saved counters\n\nauth-b 01020304 11\n",
        {"stable.cap": rows},
    )
    after = (captures / "stable.cap").read_bytes()
    assert before == after
    assert report == report2
    assert list(report.keys()) == ["accepted", "rejected", "summary"]
    assert report["accepted"] == [
        {
            "id": "stable",
            "authenticator": "auth-b",
            "credential": "01020304",
            "sign_count": 12,
            "rp_id": "login.example",
        }
    ]
    assert report["rejected"] == []
