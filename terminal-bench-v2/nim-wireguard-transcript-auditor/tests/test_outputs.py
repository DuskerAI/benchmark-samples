import json
import subprocess

import pytest


def _u16(n):
    return int(n).to_bytes(2, "big")


def _u32(n):
    return int(n).to_bytes(4, "big")


def _u64(n):
    return int(n).to_bytes(8, "big")


def _tag(context, core):
    h = 1469598103934665603
    data = b"wg-offline\0" + context + core
    for b in data:
        h ^= b
        h = (h * 1099511628211) & ((1 << 64) - 1)
    return f"{h:016x}"[8:].encode()


def _loose(core):
    h = 1469598103934665603
    data = b"wg-offline\0" + core
    for b in data:
        h ^= b
        h = (h * 1099511628211) & ((1 << 64) - 1)
    return f"{h:016x}"[8:].encode()


def _rec(kind, body, context, loose=False):
    core = bytes([kind]) + body
    mac = _loose(core) if loose else _tag(context, core)
    payload = body + mac
    return bytes([kind]) + _u16(len(payload)) + payload


def _init(sender, stamp):
    return _rec(1, _u32(sender) + _u32(stamp), b"init")


def _cookie(sender, receiver, nonce):
    return _rec(2, _u32(sender) + _u32(receiver) + _u32(nonce), b"cookie")


def _response(sender, receiver, session, key, nonce, loose=False):
    return _rec(
        3,
        _u32(sender) + _u32(receiver) + _u32(session) + _u32(key),
        b"response" + _u32(nonce),
        loose=loose,
    )


def _transport(key, counter, body, session, loose=False):
    return _rec(
        4,
        _u32(key) + _u64(counter) + _u16(len(body)) + body,
        b"transport" + _u32(session),
        loose=loose,
    )


def _transport_declares(key, counter, body, declared_len, session):
    return _rec(
        4,
        _u32(key) + _u64(counter) + _u16(declared_len) + body,
        b"transport" + _u32(session),
    )


def _rekey(key, new_key, counter, session):
    return _rec(5, _u32(key) + _u32(new_key) + _u64(counter), b"rekey" + _u32(session))


def _checkpoint(session, key, counter, previous_key=0, loose=False):
    return _rec(
        6,
        _u32(session) + _u32(key) + _u64(counter) + _u32(previous_key),
        b"checkpoint" + _u32(session),
        loose=loose,
    )


def _seal(session, key, next_counter, previous_key=0, loose=False):
    return _rec(
        7,
        _u32(session) + _u32(key) + _u64(next_counter) + _u32(previous_key),
        b"seal" + _u32(session),
        loose=loose,
    )


def _anchor(session, key, accepted_so_far, rejected_so_far, active_key_xor, loose=False):
    return _rec(
        8,
        _u32(session)
        + _u32(key)
        + _u32(accepted_so_far)
        + _u32(rejected_so_far)
        + _u32(active_key_xor),
        b"anchor" + _u32(session),
        loose=loose,
    )


def _write_bundle(path, records):
    path.write_bytes(b"WGT1" + b"".join(records))


def _build():
    subprocess.run(["/app/wgaudit/build.sh"], cwd="/app/wgaudit", check=True)


def _run_raw(captures, report):
    return subprocess.run(
        ["/app/wgaudit/bin/wgaudit", "--captures", str(captures), "--out", str(report)],
        cwd="/app/wgaudit",
        check=True,
        capture_output=True,
        text=True,
    )


def _run(captures, report):
    completed = _run_raw(captures, report)
    assert completed.stdout == ""
    assert completed.stderr == ""
    return json.loads(report.read_text())


@pytest.fixture(scope="session")
def built():
    _build()


@pytest.fixture
def simple_report(tmp_path, built):
    cap = tmp_path / "captures"
    cap.mkdir()
    stream = cap / "single.wgt"
    _write_bundle(
        stream,
        [
            _init(10, 1),
            _cookie(10, 20, 77),
            _response(20, 10, 500, 600, 77),
            _transport(600, 1, b"hello", 500),
        ],
    )
    before = stream.read_bytes()
    report = _run(cap, tmp_path / "report.json")
    return stream, before, report


@pytest.fixture
def complex_report(tmp_path, built):
    cap = tmp_path / "captures"
    cap.mkdir()
    long_body = bytes((i * 37 + 11) % 251 for i in range(300))
    _write_bundle(
        cap / "alpha.wgt",
        [
            _init(100, 1),
            _cookie(100, 200, 55),
            _response(200, 100, 777, 300, 55),
            _transport(300, 1, b"alpha", 777),
            _transport(300, 1, b"again", 777),
            _transport(300, 0, b"zero", 777),
            _transport(300, 2, long_body, 777),
            _rekey(300, 301, 3, 777),
            _transport(301, 1, b"fresh", 777),
            _transport(300, 4, b"stale", 777),
            _transport(301, 21, b"large", 777),
            _transport(301, 2, b"plain", 777, loose=True),
        ],
    )
    _write_bundle(
        cap / "beta.wgt",
        [
            _init(101, 1),
            _cookie(101, 201, 88),
            _response(201, 101, 778, 302, 88),
            _response(201, 101, 779, 303, 88),
            _transport(302, 1, b"beta", 778),
            _rekey(302, 301, 2, 778),
            _response(201, 101, 780, 304, 88, loose=True),
        ],
    )
    _write_bundle(
        cap / "gamma.wgt",
        [
            _init(110, 1),
            _cookie(110, 210, 90),
            _response(210, 110, 880, 302, 90),
        ],
    )
    return _run(cap, tmp_path / "report.json")


def _accepted(report):
    return [(x["id"], x["kind"]) for x in report["accepted"]]


def _rejected(report):
    return [(x["id"], x["kind"]) for x in report["rejected"]]


def test_admission_report_contract(simple_report):
    """The report schema, summary counts, IDs, and evidence preservation are stable."""
    stream, before, report = simple_report

    assert stream.read_bytes() == before
    assert report["summary"] == {"accepted": 4, "rejected": 0}
    assert [x["id"] for x in report["accepted"]] == [
        f"single.wgt#{i}" for i in range(4)
    ]
    assert {"accepted", "rejected", "summary"} == set(report)
    assert report["rejected"] == []


def test_accepts_valid_records_in_bytewise_order(complex_report):
    """Accepted records keep bytewise file order across independent transcripts."""
    assert _accepted(complex_report) == [
        ("alpha.wgt#0", "initiation"),
        ("alpha.wgt#1", "cookie"),
        ("alpha.wgt#2", "response"),
        ("alpha.wgt#3", "transport"),
        ("alpha.wgt#6", "transport"),
        ("alpha.wgt#7", "rekey"),
        ("alpha.wgt#8", "transport"),
        ("beta.wgt#0", "initiation"),
        ("beta.wgt#1", "cookie"),
        ("beta.wgt#2", "response"),
        ("beta.wgt#4", "transport"),
        ("gamma.wgt#0", "initiation"),
        ("gamma.wgt#1", "cookie"),
    ]


def test_rejects_replayed_and_zero_transport_counters(complex_report):
    """Transport counters are scoped to session keys and cannot be zero or repeated."""
    assert ("alpha.wgt#4", "transport") in _rejected(complex_report)
    assert ("alpha.wgt#5", "transport") in _rejected(complex_report)


def test_accepts_large_body_and_rejects_counter_limit(complex_report):
    """Body length parsing accepts long payloads but enforces the counter ceiling."""
    assert ("alpha.wgt#6", "transport") in _accepted(complex_report)
    assert ("alpha.wgt#10", "transport") in _rejected(complex_report)


def test_closes_old_key_after_rotated_key_traffic(complex_report):
    """After traffic succeeds on the rotated key, stale-key traffic is denied."""
    assert ("alpha.wgt#7", "rekey") in _accepted(complex_report)
    assert ("alpha.wgt#8", "transport") in _accepted(complex_report)
    assert ("alpha.wgt#9", "transport") in _rejected(complex_report)


def test_rejects_context_free_tags(complex_report):
    """Admission tags must include the required record context."""
    assert ("alpha.wgt#11", "transport") in _rejected(complex_report)
    assert ("beta.wgt#6", "response") in _rejected(complex_report)


def test_rejects_malformed_frame_boundaries_and_unknown_types(tmp_path, built):
    """Bad stream headers, truncated frames, and unknown record types are rejected."""
    cap = tmp_path / "captures"
    cap.mkdir()
    (cap / "bad-magic.wgt").write_bytes(b"WGTx")
    (cap / "bad-outer-length.wgt").write_bytes(
        b"WGT1" + bytes([1]) + _u16(16) + b"\x00\x01"
    )
    (cap / "short-header.wgt").write_bytes(b"WGT1" + b"\x04\x00")
    (cap / "unknown.wgt").write_bytes(b"WGT1" + bytes([9]) + _u16(0))

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == []
    assert _rejected(report) == [
        ("bad-magic.wgt#0", "unknown"),
        ("bad-outer-length.wgt#0", "initiation"),
        ("short-header.wgt#0", "unknown"),
        ("unknown.wgt#0", "unknown"),
    ]
    assert report["summary"] == {"accepted": 0, "rejected": 4}
    assert all(item.get("reason") for item in report["rejected"])


def test_rejects_transport_body_length_mismatch(tmp_path, built):
    """A transport payload whose bodyLen disagrees with its payload is denied."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "mismatch.wgt",
        [
            _init(30, 1),
            _cookie(30, 40, 123),
            _response(40, 30, 900, 901, 123),
            _transport_declares(901, 1, b"tiny", 9, 900),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("mismatch.wgt#0", "initiation"),
        ("mismatch.wgt#1", "cookie"),
        ("mismatch.wgt#2", "response"),
    ]
    assert _rejected(report) == [("mismatch.wgt#3", "transport")]
    assert report["summary"] == {"accepted": 3, "rejected": 1}


def test_successful_audit_produces_no_stdout_or_stderr(tmp_path, built):
    """The auditor's successful CLI run only writes the requested report file."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "quiet.wgt",
        [
            _init(50, 1),
            _cookie(50, 60, 77),
            _response(60, 50, 700, 701, 77),
            _transport(701, 1, b"quiet", 700),
        ],
    )
    report_path = tmp_path / "report.json"

    completed = _run_raw(cap, report_path)

    assert completed.stdout == ""
    assert completed.stderr == ""
    assert json.loads(report_path.read_text())["summary"] == {
        "accepted": 4,
        "rejected": 0,
    }


def test_consumes_cookie_and_keeps_active_keys_unique(complex_report):
    """Cookie replies and live session keys stay scoped across independent streams."""
    assert ("beta.wgt#2", "response") in _accepted(complex_report)
    assert ("beta.wgt#3", "response") in _rejected(complex_report)
    assert ("beta.wgt#5", "rekey") in _rejected(complex_report)
    assert ("gamma.wgt#2", "response") in _rejected(complex_report)


def test_rekey_counters_share_replay_state_and_chain_rotation(tmp_path, built):
    """Rekey replay checks share current-key counters across chained rotations."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "chain.wgt",
        [
            _init(70, 1),
            _cookie(70, 80, 170),
            _response(80, 70, 910, 9100, 170),
            _transport(9100, 1, b"first", 910),
            _rekey(9100, 9101, 1, 910),
            _rekey(9100, 9101, 2, 910),
            _transport(9100, 3, b"old-still-open", 910),
            _transport(9101, 1, b"new-closes-old", 910),
            _rekey(9100, 9102, 4, 910),
            _transport(9100, 4, b"old-closed", 910),
            _rekey(9101, 9102, 2, 910),
            _transport(9102, 1, b"second-generation", 910),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("chain.wgt#0", "initiation"),
        ("chain.wgt#1", "cookie"),
        ("chain.wgt#2", "response"),
        ("chain.wgt#3", "transport"),
        ("chain.wgt#5", "rekey"),
        ("chain.wgt#6", "transport"),
        ("chain.wgt#7", "transport"),
        ("chain.wgt#10", "rekey"),
        ("chain.wgt#11", "transport"),
    ]
    assert _rejected(report) == [
        ("chain.wgt#4", "rekey"),
        ("chain.wgt#8", "rekey"),
        ("chain.wgt#9", "transport"),
    ]
    assert report["summary"] == {"accepted": 9, "rejected": 3}


def test_rejects_regressing_counters_without_poisoning_later_records(tmp_path, built):
    """Transport and rekey counters must strictly increase per session key."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "counter-order.wgt",
        [
            _init(120, 1),
            _cookie(120, 220, 320),
            _response(220, 120, 420, 520, 320),
            _transport(520, 5, b"first-high", 420),
            _transport(520, 4, b"late-low", 420),
            _rekey(520, 521, 5, 420),
            _rekey(520, 521, 6, 420),
            _transport(521, 1, b"rotated", 420),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("counter-order.wgt#0", "initiation"),
        ("counter-order.wgt#1", "cookie"),
        ("counter-order.wgt#2", "response"),
        ("counter-order.wgt#3", "transport"),
        ("counter-order.wgt#6", "rekey"),
        ("counter-order.wgt#7", "transport"),
    ]
    assert _rejected(report) == [
        ("counter-order.wgt#4", "transport"),
        ("counter-order.wgt#5", "rekey"),
    ]
    assert report["summary"] == {"accepted": 6, "rejected": 2}


def test_rejects_stale_stamps_consumed_cookies_and_session_reuse(tmp_path, built):
    """Initiation stamps, cookie use, and session ids are replay barriers."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "replay-barriers.wgt",
        [
            _init(130, 10),
            _cookie(130, 230, 330),
            _response(230, 130, 430, 530, 330),
            _response(230, 130, 431, 531, 330),
            _init(130, 9),
            _init(130, 11),
            _cookie(130, 230, 331),
            _response(230, 130, 430, 532, 331),
            _cookie(130, 230, 332),
            _response(230, 130, 431, 530, 332),
            _cookie(130, 230, 333),
            _response(230, 130, 431, 531, 333),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("replay-barriers.wgt#0", "initiation"),
        ("replay-barriers.wgt#1", "cookie"),
        ("replay-barriers.wgt#2", "response"),
        ("replay-barriers.wgt#5", "initiation"),
        ("replay-barriers.wgt#6", "cookie"),
        ("replay-barriers.wgt#8", "cookie"),
        ("replay-barriers.wgt#10", "cookie"),
        ("replay-barriers.wgt#11", "response"),
    ]
    assert _rejected(report) == [
        ("replay-barriers.wgt#3", "response"),
        ("replay-barriers.wgt#4", "initiation"),
        ("replay-barriers.wgt#7", "response"),
        ("replay-barriers.wgt#9", "response"),
    ]
    assert report["summary"] == {"accepted": 8, "rejected": 4}


def test_cookie_stamp_epoch_and_nonce_replay_barriers(tmp_path, built):
    """Cookie nonces and initiation stamp epochs gate later responses."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "cookie-epochs.wgt",
        [
            _init(160, 10),
            _cookie(160, 260, 360),
            _init(160, 11),
            _response(260, 160, 460, 560, 360),
            _cookie(160, 260, 360),
            _cookie(160, 260, 361),
            _response(260, 160, 461, 561, 361),
            _response(260, 160, 462, 562, 361),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("cookie-epochs.wgt#0", "initiation"),
        ("cookie-epochs.wgt#1", "cookie"),
        ("cookie-epochs.wgt#2", "initiation"),
        ("cookie-epochs.wgt#5", "cookie"),
        ("cookie-epochs.wgt#6", "response"),
    ]
    assert _rejected(report) == [
        ("cookie-epochs.wgt#3", "response"),
        ("cookie-epochs.wgt#4", "cookie"),
        ("cookie-epochs.wgt#7", "response"),
    ]
    assert report["summary"] == {"accepted": 5, "rejected": 3}


def test_retired_session_keys_cannot_be_reintroduced(tmp_path, built):
    """A closed session key remains retired even after further valid rotation."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "retired-key.wgt",
        [
            _init(140, 1),
            _cookie(140, 240, 340),
            _response(240, 140, 440, 540, 340),
            _rekey(540, 541, 1, 440),
            _transport(541, 1, b"close-old", 440),
            _rekey(541, 540, 2, 440),
            _rekey(541, 542, 2, 440),
            _transport(542, 1, b"next", 440),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("retired-key.wgt#0", "initiation"),
        ("retired-key.wgt#1", "cookie"),
        ("retired-key.wgt#2", "response"),
        ("retired-key.wgt#3", "rekey"),
        ("retired-key.wgt#4", "transport"),
        ("retired-key.wgt#6", "rekey"),
        ("retired-key.wgt#7", "transport"),
    ]
    assert _rejected(report) == [("retired-key.wgt#5", "rekey")]
    assert report["summary"] == {"accepted": 7, "rejected": 1}


def test_retired_keys_are_global_admission_barriers(tmp_path, built):
    """Retired keys cannot be reused by later sessions or rekey chains."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "global-retire.wgt",
        [
            _init(180, 1),
            _cookie(180, 280, 380),
            _response(280, 180, 480, 580, 380),
            _rekey(580, 581, 1, 480),
            _transport(581, 1, b"retires-580", 480),
            _init(181, 1),
            _cookie(181, 281, 381),
            _response(281, 181, 481, 580, 381),
            _response(281, 181, 481, 582, 381),
            _rekey(582, 580, 1, 481),
            _rekey(582, 583, 1, 481),
            _transport(583, 1, b"fresh", 481),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("global-retire.wgt#0", "initiation"),
        ("global-retire.wgt#1", "cookie"),
        ("global-retire.wgt#2", "response"),
        ("global-retire.wgt#3", "rekey"),
        ("global-retire.wgt#4", "transport"),
        ("global-retire.wgt#5", "initiation"),
        ("global-retire.wgt#6", "cookie"),
        ("global-retire.wgt#8", "response"),
        ("global-retire.wgt#10", "rekey"),
        ("global-retire.wgt#11", "transport"),
    ]
    assert _rejected(report) == [
        ("global-retire.wgt#7", "response"),
        ("global-retire.wgt#9", "rekey"),
    ]
    assert report["summary"] == {"accepted": 10, "rejected": 2}


def test_response_spends_pending_initiation_epoch(tmp_path, built):
    """A response spends its initiation epoch, so later cookies need a newer stamp."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "pending-epoch.wgt",
        [
            _init(170, 10),
            _cookie(170, 270, 370),
            _response(270, 170, 470, 570, 370),
            _cookie(170, 270, 371),
            _response(270, 170, 471, 571, 371),
            _init(170, 11),
            _cookie(170, 270, 372),
            _response(270, 170, 471, 571, 372),
            _transport(571, 1, b"next-epoch", 471),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("pending-epoch.wgt#0", "initiation"),
        ("pending-epoch.wgt#1", "cookie"),
        ("pending-epoch.wgt#2", "response"),
        ("pending-epoch.wgt#5", "initiation"),
        ("pending-epoch.wgt#6", "cookie"),
        ("pending-epoch.wgt#7", "response"),
        ("pending-epoch.wgt#8", "transport"),
    ]
    assert _rejected(report) == [
        ("pending-epoch.wgt#3", "cookie"),
        ("pending-epoch.wgt#4", "response"),
    ]
    assert report["summary"] == {"accepted": 7, "rejected": 2}


def test_recursive_bytewise_order_and_empty_transport_body(tmp_path, built):
    """Recursive capture discovery uses bytewise paths and accepts empty bodies."""
    cap = tmp_path / "captures"
    (cap / "aa").mkdir(parents=True)
    (cap / "b").mkdir()
    _write_bundle(
        cap / "b" / "later.wgt",
        [
            _init(91, 1),
            _cookie(91, 92, 193),
            _response(92, 91, 993, 994, 193),
            _transport(994, 1, b"", 993),
        ],
    )
    _write_bundle(
        cap / "aa" / "first.wgt",
        [
            _init(81, 1),
            _cookie(81, 82, 183),
            _response(82, 81, 983, 984, 183),
            _transport(984, 1, b"", 983),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("aa/first.wgt#0", "initiation"),
        ("aa/first.wgt#1", "cookie"),
        ("aa/first.wgt#2", "response"),
        ("aa/first.wgt#3", "transport"),
        ("b/later.wgt#0", "initiation"),
        ("b/later.wgt#1", "cookie"),
        ("b/later.wgt#2", "response"),
        ("b/later.wgt#3", "transport"),
    ]
    assert _rejected(report) == []
    assert report["summary"] == {"accepted": 8, "rejected": 0}



def test_checkpoints_and_seals_attest_rotation_epoch(tmp_path, built):
    """Checkpoint and seal records attest current-key counter windows."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "checkpoint.wgt",
        [
            _init(190, 1),
            _cookie(190, 290, 390),
            _response(290, 190, 490, 590, 390),
            _checkpoint(490, 590, 0),
            _transport(590, 4, b"high", 490),
            _checkpoint(490, 590, 3),
            _checkpoint(490, 590, 4),
            _rekey(590, 591, 5, 490),
            _checkpoint(490, 590, 5, 590),
            _checkpoint(490, 591, 0, 590),
            _checkpoint(490, 591, 1, 590),
            _transport(591, 1, b"close", 490),
            _checkpoint(490, 591, 1, 590),
            _checkpoint(490, 591, 1, 0),
            _seal(490, 591, 2),
            _seal(490, 591, 1),
            _seal(490, 590, 6),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("checkpoint.wgt#0", "initiation"),
        ("checkpoint.wgt#1", "cookie"),
        ("checkpoint.wgt#2", "response"),
        ("checkpoint.wgt#3", "checkpoint"),
        ("checkpoint.wgt#4", "transport"),
        ("checkpoint.wgt#6", "checkpoint"),
        ("checkpoint.wgt#7", "rekey"),
        ("checkpoint.wgt#9", "checkpoint"),
        ("checkpoint.wgt#11", "transport"),
        ("checkpoint.wgt#13", "checkpoint"),
        ("checkpoint.wgt#14", "seal"),
    ]
    assert _rejected(report) == [
        ("checkpoint.wgt#5", "checkpoint"),
        ("checkpoint.wgt#8", "checkpoint"),
        ("checkpoint.wgt#10", "checkpoint"),
        ("checkpoint.wgt#12", "checkpoint"),
        ("checkpoint.wgt#15", "seal"),
        ("checkpoint.wgt#16", "seal"),
    ]
    assert report["summary"] == {"accepted": 11, "rejected": 6}


def test_checkpoint_authentication_and_malformed_bodies(tmp_path, built):
    """Checkpoint tags use session context; malformed attestations are denied."""
    cap = tmp_path / "captures"
    cap.mkdir()
    valid = _checkpoint(510, 610, 0)
    _write_bundle(
        cap / "bad-checkpoint.wgt",
        [
            _init(191, 1),
            _cookie(191, 291, 391),
            _response(291, 191, 510, 610, 391),
            _checkpoint(510, 610, 0, loose=True),
            _seal(510, 610, 1, loose=True),
            bytes([6]) + _u16(10) + valid[3:13],
            _checkpoint(511, 610, 0),
            _checkpoint(510, 610, 0),
            _seal(510, 610, 1),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("bad-checkpoint.wgt#0", "initiation"),
        ("bad-checkpoint.wgt#1", "cookie"),
        ("bad-checkpoint.wgt#2", "response"),
        ("bad-checkpoint.wgt#7", "checkpoint"),
        ("bad-checkpoint.wgt#8", "seal"),
    ]
    assert _rejected(report) == [
        ("bad-checkpoint.wgt#3", "checkpoint"),
        ("bad-checkpoint.wgt#4", "seal"),
        ("bad-checkpoint.wgt#5", "checkpoint"),
        ("bad-checkpoint.wgt#6", "checkpoint"),
    ]
    assert all(item["reason"] for item in report["rejected"])


def test_anchor_records_attest_prior_decisions_and_live_key_set(tmp_path, built):
    """Anchor records bind the current transcript counts and usable key frontier."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "anchors.wgt",
        [
            _init(210, 1),
            _cookie(210, 310, 410),
            _response(310, 210, 510, 610, 410),
            _anchor(510, 610, 3, 0, 610),
            _rekey(610, 611, 1, 510),
            _anchor(510, 611, 5, 0, 1),
            _transport(611, 1, b"close", 510),
            _anchor(510, 611, 7, 0, 611),
            _anchor(510, 611, 99, 0, 611),
            _anchor(510, 610, 7, 1, 611),
            _anchor(510, 611, 7, 2, 1),
            _anchor(510, 611, 7, 3, 611, loose=True),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("anchors.wgt#0", "initiation"),
        ("anchors.wgt#1", "cookie"),
        ("anchors.wgt#2", "response"),
        ("anchors.wgt#3", "anchor"),
        ("anchors.wgt#4", "rekey"),
        ("anchors.wgt#5", "anchor"),
        ("anchors.wgt#6", "transport"),
        ("anchors.wgt#7", "anchor"),
    ]
    assert _rejected(report) == [
        ("anchors.wgt#8", "anchor"),
        ("anchors.wgt#9", "anchor"),
        ("anchors.wgt#10", "anchor"),
        ("anchors.wgt#11", "anchor"),
    ]
    assert report["summary"] == {"accepted": 8, "rejected": 4}


def test_anchor_decisions_share_bytewise_order_with_parse_failures(tmp_path, built):
    """Anchors use global decision counts after earlier files and parse failures."""
    cap = tmp_path / "captures"
    (cap / "aa").mkdir(parents=True)
    _write_bundle(
        cap / "aa" / "first.wgt",
        [
            _init(220, 1),
            _cookie(220, 320, 420),
            _response(320, 220, 520, 620, 420),
        ],
    )
    (cap / "bb-bad.wgt").write_bytes(b"WGT1" + bytes([8]) + _u16(28) + b"short")
    _write_bundle(cap / "cc-anchor.wgt", [_anchor(520, 620, 3, 1, 620)])

    report = _run(cap, tmp_path / "report.json")

    assert _accepted(report) == [
        ("aa/first.wgt#0", "initiation"),
        ("aa/first.wgt#1", "cookie"),
        ("aa/first.wgt#2", "response"),
        ("cc-anchor.wgt#0", "anchor"),
    ]
    assert _rejected(report) == [("bb-bad.wgt#0", "anchor")]
    assert report["summary"] == {"accepted": 4, "rejected": 1}

def test_rejected_records_include_reasons_and_summary(complex_report):
    """Rejected records carry reasons and summary counts match the decision arrays."""
    accepted = _accepted(complex_report)
    rejected = _rejected(complex_report)
    assert rejected == [
        ("alpha.wgt#4", "transport"),
        ("alpha.wgt#5", "transport"),
        ("alpha.wgt#9", "transport"),
        ("alpha.wgt#10", "transport"),
        ("alpha.wgt#11", "transport"),
        ("beta.wgt#3", "response"),
        ("beta.wgt#5", "rekey"),
        ("beta.wgt#6", "response"),
        ("gamma.wgt#2", "response"),
    ]
    assert complex_report["summary"] == {
        "accepted": len(accepted),
        "rejected": len(rejected),
    }
    assert all(
        "reason" in item and item["reason"] for item in complex_report["rejected"]
    )


def test_mixed_rejections_keep_bytewise_decision_order(tmp_path, built):
    """Malformed streams and semantic denials share bytewise decision ordering."""
    cap = tmp_path / "captures"
    cap.mkdir()
    _write_bundle(
        cap / "aa-state.wgt",
        [
            _init(150, 1),
            _cookie(150, 250, 350),
            _response(250, 150, 450, 550, 350),
            _transport(550, 1, b"ok", 450),
            _transport(550, 1, b"replay", 450),
        ],
    )
    (cap / "bb-bad.wgt").write_bytes(b"WGT1" + bytes([4]) + _u16(40) + b"short")
    _write_bundle(
        cap / "cc-state.wgt",
        [
            _init(151, 1),
            _cookie(151, 251, 351),
            _response(251, 151, 451, 551, 351),
            _transport(551, 0, b"zero", 451),
        ],
    )

    report = _run(cap, tmp_path / "report.json")

    assert _rejected(report) == [
        ("aa-state.wgt#4", "transport"),
        ("bb-bad.wgt#0", "transport"),
        ("cc-state.wgt#3", "transport"),
    ]
    assert report["summary"] == {"accepted": 7, "rejected": 3}


def test_admission_decisions_across_transcripts(complex_report):
    """The complete decision set remains stable as an end-to-end guardrail."""
    accepted = _accepted(complex_report)
    rejected = _rejected(complex_report)
    assert accepted == [
        ("alpha.wgt#0", "initiation"),
        ("alpha.wgt#1", "cookie"),
        ("alpha.wgt#2", "response"),
        ("alpha.wgt#3", "transport"),
        ("alpha.wgt#6", "transport"),
        ("alpha.wgt#7", "rekey"),
        ("alpha.wgt#8", "transport"),
        ("beta.wgt#0", "initiation"),
        ("beta.wgt#1", "cookie"),
        ("beta.wgt#2", "response"),
        ("beta.wgt#4", "transport"),
        ("gamma.wgt#0", "initiation"),
        ("gamma.wgt#1", "cookie"),
    ]
    assert rejected == [
        ("alpha.wgt#4", "transport"),
        ("alpha.wgt#5", "transport"),
        ("alpha.wgt#9", "transport"),
        ("alpha.wgt#10", "transport"),
        ("alpha.wgt#11", "transport"),
        ("beta.wgt#3", "response"),
        ("beta.wgt#5", "rekey"),
        ("beta.wgt#6", "response"),
        ("gamma.wgt#2", "response"),
    ]
