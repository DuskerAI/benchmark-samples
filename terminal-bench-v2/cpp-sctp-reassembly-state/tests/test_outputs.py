"""Behavioral verifier for the SCTP receiver trace tool."""

import hashlib
import json
import subprocess
from pathlib import Path


APP = Path("/app/task_file")
BIN = APP / "build" / "sctp_replay"

CASES = {
    "alpha": (
        """START 100
DATA 101 0 1 BE 7 6f6e65
DATA 103 1 0 BEU 8 756e
DATA 100 0 0 BE 7 7a65726f
DATA 103 1 0 BEU 8 626164
ABANDON 102 102
""",
        "6cc43ba5cfdcc4b43b764189881122033285634d0a15c7d9a698c5692ff2d538",
    ),
    "bravo": (
        """START 200
IDATA 200 2 0 0 B 30 aa
IDATA 202 2 0 2 E 32 cc
IDATA 201 2 1 0 BEU 40 ff
IDATA 203 2 0 1 0 31 bb
""",
        "d703c038841b55c95124ee658fb8f7e70d26498c2c086aa64620b18643c901a0",
    ),
    "charlie": (
        """START 300
DATA 300 3 0 B 50 aa
DATA 302 3 1 BE 51 bb
ABANDON 301 301
""",
        "ccd64d64a371d0a11763756e1cc78fd69e72c76c3234be67315f25c01dd7e903",
    ),
    "delta": (
        """START 400
DATA 400 4 0 BE 60 00
DATA 401 4 1 B 61 11
RESET 4 5 7
DATA 402 4 4 BE 62 22
IDATA 403 4 7 0 BE 70 33
""",
        "f4145293a39fd920e5b0b018bb79cce54c02276e2c0d6ad10c2c24b22faa2f29",
    ),
    "echo": (
        """START 500
IDATA 500 5 0 0 B 10 aa
IDATA 502 5 0 2 E 12 cc
IDATA 503 5 1 0 BE 13 dd
ABANDON 501 501
DATA 504 6 0 BU 20 ee
ABANDON 505 505
DATA 506 7 0 BE 30 ff
""",
        "5346bd45298d702612b5ca960cf4af84d62931b5f4c19543b8ad73fdcf41e090",
    ),
    "foxtrot": (
        """START 600
DATA 600 8 0 B 40 aa
RESET 8 2 9
DATA 601 8 0 E 41 bb
IDATA 602 8 8 0 B 50 cc
DATA 603 8 2 BE 42 dd
IDATA 604 8 9 0 BE 52 ee
""",
        "e671960a0e0547089776775c0f49c7839b4e679d353220a5b5c68e13ca819892",
    ),
    "golf": (
        """START 700
DATA 700 10 0 BE 90 aa
DATA 702 10 1 BE 91 bb
""",
        "83c34e2d4cb297af6190f659b3717510cae8cf5c7da3a769487922c0011a9df3",
    ),
    "hotel": (
        """START 800
IDATA 800 11 0 0 B 100 aa
IDATA 801 11 0 0 B 101 ffff
IDATA 802 11 0 1 E 102 bb
""",
        "c318f45d4913b4f23ea82179087fe20a3ab61ee2a7d9a3a28c5cec293c07fb9f",
    ),
    "india": (
        """START 900
DATA 900 12 0 B 110 aa
IDATA 901 12 0 0 B 120 bb
ABANDON 901 901
DATA 902 12 1 BE 111 cc
IDATA 903 12 1 0 BE 121 dd
""",
        "b7f7cc8ce3ab08b4dca672d40631357cc266a3ac55394cb79c241aa3e7338d62",
    ),
    "juliet": (
        """START 1000
DATA 1001 20 3 BEU 3 aa
DATA 1002 21 0 B 4 bb
ABANDON 1000 1003
""",
        "c5e774d42ed7f74b6f911cf6687486ebf02fc6561579660f9d267a8a4d0dfc82",
    ),
    "kilo": (
        """START 1100
IDATA 1102 22 0 2 E 0 cc
IDATA 1100 22 0 0 B 5 aa
IDATA 1101 22 0 1 0 6 bb
""",
        "79f165500a2b0f886a4f581a611a3b07cb6856aa97bded6fbcbedae2678613c7",
    ),
    "lima": (
        """START 1200
IDATA 1201 23 0 0 B 80 ffff
IDATA 1200 23 0 0 B 81 aa
IDATA 1202 23 0 1 E 82 bb
""",
        "63033721f63ea38e037e87f0ac883d5d832b3c098a2bb7062208aae93e8a4408",
    ),
    "mike": (
        """START 4294967294
DATA 1 24 0 BE 9 dd
ABANDON 4294967294 0
""",
        "cb63f3e72a8d852afb42eff3c8e3c9791efaa2544b065ba344ee079d8c00753c",
    ),
    "november": (
        """   # leading-space comment
START 1300
DATA 1300 25 0 B 1 AA
   # another comment
DATA 1301 25 0 E 0 BB
""",
        "3fcb8e9d250ab513feca4ae4affcc7d278bbd8b00f29da913d0350f4e38c63a5",
    ),
}


def build_binary() -> None:
    subprocess.run(
        ["make", "clean", "all"],
        cwd=APP,
        check=True,
        text=True,
        capture_output=True,
    )


def run_case(tmp_path: Path, name: str, trace: str) -> tuple[dict, str]:
    input_path = tmp_path / f"{name}.trace"
    output_path = tmp_path / f"{name}.json"
    input_path.write_text(trace)
    subprocess.run(
        [str(BIN), str(input_path), str(output_path)],
        cwd=APP,
        check=True,
        text=True,
        capture_output=True,
    )
    data = json.loads(output_path.read_text())
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return data, hashlib.sha256(canonical).hexdigest()


def assert_common_contract(
    data: dict,
    *,
    expected_cumulative: int | None = None,
    expected_buffered: int = 0,
    expected_pending: int = 0,
) -> None:
    assert set(data) == {"events", "state"}
    assert isinstance(data["events"], list)
    assert set(data["state"]) == {
        "cumulative_tsn",
        "buffered_chunks",
        "pending_messages",
    }
    if expected_cumulative is not None:
        assert data["state"]["cumulative_tsn"] == expected_cumulative
    assert data["state"]["buffered_chunks"] == expected_buffered
    assert data["state"]["pending_messages"] == expected_pending


def assert_expected_hash(data: dict, expected_hash: str, digest: str) -> None:
    assert digest == expected_hash, (
        f"hash mismatch\nexpected: {expected_hash}\ngot:      {digest}\n"
        f"events: {json.dumps(data['events'], indent=2, sort_keys=True)}"
    )


def run_named_case(
    tmp_path: Path,
    name: str,
    *,
    expected_cumulative: int | None = None,
    expected_buffered: int = 0,
    expected_pending: int = 0,
) -> tuple[dict, str, str]:
    trace, expected_hash = CASES[name]
    data, digest = run_case(tmp_path, name, trace)
    assert_common_contract(
        data,
        expected_cumulative=expected_cumulative,
        expected_buffered=expected_buffered,
        expected_pending=expected_pending,
    )
    return data, digest, expected_hash


def test_project_builds_cleanly():
    """The C++ project should compile with the provided Makefile."""
    build_binary()
    assert BIN.exists()


def test_alpha_orders_duplicates_and_abandon_frontier(tmp_path):
    """Classic DATA delivery holds gaps, ignores duplicates, and resumes."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "alpha")
    assert [event["type"] for event in data["events"]] == [
        "deliver",
        "deliver",
        "deliver",
    ]
    assert [event["id"] for event in data["events"]] == [0, 1, 0]
    assert data["events"][-1]["ordered"] is False
    assert data["events"][-1]["payload_hex"] == "756e"
    assert_expected_hash(data, expected_hash, digest)


def test_bravo_reassembles_idata_by_fsn(tmp_path):
    """I-DATA accepts an intermediate flag and concatenates by FSN."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "bravo")
    assert [event["ordered"] for event in data["events"]] == [False, True]
    assert [event["id"] for event in data["events"]] == [1, 0]
    assert data["events"][1]["payload_hex"] == "aabbcc"
    assert data["events"][1]["first_tsn"] == 200
    assert data["events"][1]["last_tsn"] == 203
    assert_expected_hash(data, expected_hash, digest)


def test_charlie_skips_abandoned_ordered_message(tmp_path):
    """An abandoned gap in the next ordered message emits a skip first."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "charlie")
    assert [event["type"] for event in data["events"]] == ["skip", "deliver"]
    assert data["events"][0] == {
        "type": "skip",
        "format": "data",
        "stream": 3,
        "id": 0,
        "reason": "abandoned",
    }
    assert data["events"][1]["id"] == 1
    assert_expected_hash(data, expected_hash, digest)


def test_delta_reset_drops_buffers_and_advances_ids(tmp_path):
    """RESET drops buffered stream state and applies new expected IDs."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "delta")
    assert [event["type"] for event in data["events"]] == [
        "deliver",
        "reset",
        "deliver",
    ]
    assert data["events"][1] == {
        "type": "reset",
        "stream": 4,
        "next_ssn": 5,
        "next_mid": 7,
    }
    assert data["events"][2]["format"] == "idata"
    assert data["events"][2]["id"] == 7
    assert_expected_hash(data, expected_hash, digest)


def test_echo_abandon_skips_idata_and_discards_unordered(tmp_path):
    """ABANDON fails ordered I-DATA but drops unordered partial messages."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "echo")
    assert [event["type"] for event in data["events"]] == [
        "skip",
        "deliver",
        "deliver",
    ]
    assert data["events"][0] == {
        "type": "skip",
        "format": "idata",
        "stream": 5,
        "id": 0,
        "reason": "abandoned",
    }
    assert data["events"][1]["format"] == "idata"
    assert data["events"][1]["id"] == 1
    assert data["events"][2]["stream"] == 7
    assert_expected_hash(data, expected_hash, digest)


def test_foxtrot_ignores_stale_chunks_after_reset(tmp_path):
    """Stale ordered DATA and I-DATA chunks after RESET do not reopen state."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "foxtrot")
    assert [event["type"] for event in data["events"]] == [
        "reset",
        "deliver",
        "deliver",
    ]
    assert data["events"][0] == {
        "type": "reset",
        "stream": 8,
        "next_ssn": 2,
        "next_mid": 9,
    }
    assert [event["id"] for event in data["events"][1:]] == [2, 9]
    assert [event["format"] for event in data["events"][1:]] == ["data", "idata"]
    assert_expected_hash(data, expected_hash, digest)


def test_golf_reports_pending_tail_state(tmp_path):
    """A committed prefix can deliver while a later gap remains buffered."""
    build_binary()
    data, digest, expected_hash = run_named_case(
        tmp_path,
        "golf",
        expected_cumulative=700,
        expected_buffered=1,
        expected_pending=1,
    )
    assert len(data["events"]) == 1
    assert data["events"][0]["id"] == 0
    assert data["events"][0]["payload_hex"] == "aa"
    assert_expected_hash(data, expected_hash, digest)


def test_hotel_keeps_first_idata_fsn_duplicate(tmp_path):
    """A later I-DATA fragment at an occupied FSN advances TSN only."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "hotel")
    assert len(data["events"]) == 1
    assert data["events"][0]["format"] == "idata"
    assert data["events"][0]["payload_hex"] == "aabb"
    assert data["events"][0]["ppid"] == 102
    assert data["events"][0]["first_tsn"] == 800
    assert data["events"][0]["last_tsn"] == 802
    assert_expected_hash(data, expected_hash, digest)


def test_india_cascades_ordered_skips_across_formats(tmp_path):
    """One abandonment can release independent DATA and I-DATA order spaces."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "india")
    assert [event["type"] for event in data["events"]] == [
        "skip",
        "skip",
        "deliver",
        "deliver",
    ]
    assert data["events"][0]["format"] == "data"
    assert data["events"][1]["format"] == "idata"
    assert [event["id"] for event in data["events"]] == [0, 0, 1, 1]
    assert_expected_hash(data, expected_hash, digest)


def test_juliet_merges_deliver_and_skip_candidate_order(tmp_path):
    """Ready deliveries and skips share the same deterministic ordering pass."""
    build_binary()
    data, digest, expected_hash = run_named_case(
        tmp_path,
        "juliet",
        expected_cumulative=1003,
    )
    assert [event["type"] for event in data["events"]] == ["deliver", "skip"]
    assert data["events"][0]["ordered"] is False
    assert data["events"][0]["stream"] == 20
    assert data["events"][1] == {
        "type": "skip",
        "format": "data",
        "stream": 21,
        "id": 0,
        "reason": "abandoned",
    }
    assert_expected_hash(data, expected_hash, digest)


def test_kilo_uses_ending_fragment_ppid_even_when_zero(tmp_path):
    """The ending fragment owns the PPID, including the decimal value zero."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "kilo")
    assert len(data["events"]) == 1
    assert data["events"][0]["format"] == "idata"
    assert data["events"][0]["ppid"] == 0
    assert data["events"][0]["payload_hex"] == "aabbcc"
    assert_expected_hash(data, expected_hash, digest)


def test_lima_keeps_lower_tsn_duplicate_fsn_even_if_seen_later(tmp_path):
    """For duplicate I-DATA FSNs, the lower TSN fragment owns assembly."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "lima")
    assert len(data["events"]) == 1
    assert data["events"][0]["payload_hex"] == "aabb"
    assert data["events"][0]["first_tsn"] == 1200
    assert data["events"][0]["last_tsn"] == 1202
    assert_expected_hash(data, expected_hash, digest)


def test_mike_advances_tsn_frontier_across_uint32_wrap(tmp_path):
    """Wrapped ABANDON ranges advance the 32-bit TSN frontier."""
    build_binary()
    data, digest, expected_hash = run_named_case(
        tmp_path,
        "mike",
        expected_cumulative=1,
    )
    assert len(data["events"]) == 1
    assert data["events"][0]["first_tsn"] == 1
    assert data["events"][0]["last_tsn"] == 1
    assert_expected_hash(data, expected_hash, digest)


def test_november_accepts_whitespace_comments_and_normalizes_hex(tmp_path):
    """Whole-line comments may be indented and payload hex is lowercase JSON."""
    build_binary()
    data, digest, expected_hash = run_named_case(tmp_path, "november")
    assert len(data["events"]) == 1
    assert data["events"][0]["payload_hex"] == "aabb"
    assert data["events"][0]["ppid"] == 0
    assert_expected_hash(data, expected_hash, digest)
