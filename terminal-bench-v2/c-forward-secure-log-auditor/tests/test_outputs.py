import hashlib
import hmac
import json
import subprocess
from collections import OrderedDict
from pathlib import Path

import pytest


ROOT = Path("/app/forwardlog")
PROGRAM = ROOT / "bin/flogaudit"


def sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def merkle(nodes: list[bytes]) -> bytes:
    level = list(nodes)
    while len(level) > 1:
        nxt = []
        for pos in range(0, len(level), 2):
            if pos + 1 == len(level):
                nxt.append(level[pos])
            else:
                nxt.append(sha(b"FLOG-NODE\0" + level[pos] + level[pos + 1]))
        level = nxt
    return level[0]


class Archive:
    def __init__(self, stream: str = "case-a", key_count: int = 4, max_tail: int = 64):
        self.stream = stream
        self.keys = [sha(f"forwardlog-key-{i}".encode()) for i in range(key_count)]
        self.genesis = sha(b"forwardlog-genesis:" + stream.encode())
        self.max_tail = max_tail
        self.chain = self.genesis
        self.seq = 0
        self.epoch = 0
        self.leaves: list[tuple[int, bytes]] = []
        self.lines: list[str] = []

    def policy(self) -> bytes:
        lines = [
            "FSPOLICY\t1",
            f"STREAM\t{self.stream}",
            f"GENESIS\t{self.genesis.hex()}",
            f"MAX_TAIL\t{self.max_tail}",
        ]
        lines.extend(f"KEY\t{i}\t{key.hex()}" for i, key in enumerate(self.keys))
        lines.append("END")
        return ("\n".join(lines) + "\n").encode()

    def _append(self, body: str, key_epoch: int, leaf: bool) -> bytes:
        old = self.chain
        body_bytes = body.encode("ascii")
        record_hash = sha(b"FLOG-RECORD\0" + old + len(body_bytes).to_bytes(4, "big") + body_bytes)
        tag = hmac.new(
            self.keys[key_epoch],
            b"FLOG-MAC\0" + self.stream.encode() + b"\0" + record_hash,
            hashlib.sha256,
        ).digest()
        self.lines.append(f"{body}\t{old.hex()}\t{tag.hex()}")
        self.chain = record_hash
        if leaf:
            self.leaves.append((self.seq, record_hash))
        return record_hash

    def event(self, timestamp: int, payload: bytes, payload_text: str | None = None) -> None:
        self.seq += 1
        encoded = payload.hex() if payload_text is None else payload_text
        self._append(f"E\t{self.seq}\t{self.epoch}\t{timestamp}\t{encoded}", self.epoch, True)

    def rotate(self, commitment: bytes | None = None) -> None:
        self.seq += 1
        new_epoch = self.epoch + 1
        if commitment is None:
            commitment = sha(b"FLOG-KEY\0" + new_epoch.to_bytes(8, "big") + self.keys[new_epoch])
        self._append(
            f"R\t{self.seq}\t{self.epoch}\t{new_epoch}\t{commitment.hex()}",
            self.epoch,
            True,
        )
        self.epoch = new_epoch

    def checkpoint(self, root: bytes | None = None) -> None:
        self.seq += 1
        if root is None:
            root = merkle([item[1] for item in self.leaves])
        first, last, count = self.leaves[0][0], self.leaves[-1][0], len(self.leaves)
        self._append(
            f"C\t{self.seq}\t{self.epoch}\t{first}\t{last}\t{count}\t{root.hex()}",
            self.epoch,
            False,
        )
        self.leaves.clear()

    def log(self, tail: bytes = b"") -> bytes:
        committed = (("\n".join(self.lines) + "\n").encode() if self.lines else b"")
        return committed + tail


@pytest.fixture(scope="session", autouse=True)
def build_program() -> None:
    """The shipped C project builds cleanly before behavioral checks run."""
    result = subprocess.run(
        ["make", "clean", "all"], cwd=ROOT, text=True, capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert PROGRAM.is_file()


def invoke(tmp_path: Path, archive: Archive, log: bytes | None = None, policy: bytes | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    policy_path = tmp_path / "policy.fsp"
    log_path = tmp_path / "evidence.flog"
    report_path = tmp_path / "report.json"
    policy_bytes = archive.policy() if policy is None else policy
    log_bytes = archive.log() if log is None else log
    policy_path.write_bytes(policy_bytes)
    log_path.write_bytes(log_bytes)
    report_path.write_text("stale report\n")
    result = subprocess.run(
        [
            str(PROGRAM),
            "--policy",
            str(policy_path),
            "--log",
            str(log_path),
            "--report",
            str(report_path),
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.stdout == ""
    assert result.stderr == ""
    assert policy_path.read_bytes() == policy_bytes
    assert log_path.read_bytes() == log_bytes
    assert report_path.is_file()
    raw = report_path.read_text()
    assert raw.endswith("\n") and raw.count("\n") == 1
    report = json.loads(raw, object_pairs_hook=OrderedDict)
    assert list(report) == [
        "admitted",
        "reason",
        "stream",
        "committed_records",
        "last_sequence",
        "epoch",
        "chain_hash",
        "last_checkpoint",
        "recovered_tail_bytes",
    ]
    assert set(tmp_path.iterdir()) == {policy_path, log_path, report_path}
    return result, report


def assert_denied(tmp_path: Path, archive: Archive, reason: str, log: bytes | None = None, policy: bytes | None = None):
    result, report = invoke(tmp_path, archive, log=log, policy=policy)
    assert result.returncode == 2
    assert report["admitted"] is False
    assert report["reason"] == reason
    return report


def test_canonical_admission_report(tmp_path: Path) -> None:
    """A canonical sealed archive is admitted with exact terminal state."""
    a = Archive()
    a.event(10, b"login")
    a.event(11, b"read")
    a.checkpoint()
    result, report = invoke(tmp_path, a)
    assert result.returncode == 0
    assert report == OrderedDict(
        admitted=True,
        reason="OK",
        stream=a.stream,
        committed_records=3,
        last_sequence=3,
        epoch=0,
        chain_hash=a.chain.hex(),
        last_checkpoint=3,
        recovered_tail_bytes=0,
    )

    for index, boundary in enumerate(
        [Archive(stream="a", key_count=1), Archive(stream="s" * 32, key_count=32)]
    ):
        boundary.event(1, b"boundary")
        boundary.checkpoint()
        result, report = invoke(tmp_path / f"boundary-{index}", boundary)
        assert result.returncode == 0
        assert report["stream"] == boundary.stream


def test_checkpoint_admission(tmp_path: Path) -> None:
    """An odd checkpoint frontier uses promotion and remains admissible."""
    a = Archive(stream="case-b")
    for i in range(5):
        a.event(100 + i, f"item-{i}".encode())
    a.checkpoint()
    result, report = invoke(tmp_path, a)
    assert result.returncode == 0
    assert report["committed_records"] == 6
    assert report["chain_hash"] == a.chain.hex()


def test_epoch_admission(tmp_path: Path) -> None:
    """Rotations authenticate under the departing epoch and advance state afterward."""
    a = Archive(stream="case-c")
    a.event(1, b"one")
    a.rotate()
    a.event(2, b"two")
    a.rotate()
    a.event(3, b"three")
    a.checkpoint()
    result, report = invoke(tmp_path, a)
    assert result.returncode == 0
    assert report["epoch"] == 2
    assert report["last_sequence"] == 6


def test_segmented_archive_admission(tmp_path: Path) -> None:
    """Several checkpoints independently cover only leaves since the prior checkpoint."""
    a = Archive(stream="case-d")
    a.event(1, b"a")
    a.checkpoint()
    a.event(2, b"b")
    a.event(3, b"c")
    a.checkpoint()
    a.rotate()
    a.event(4, b"d")
    a.checkpoint()
    result, report = invoke(tmp_path, a)
    assert result.returncode == 0
    assert report["last_checkpoint"] == 8
    assert report["committed_records"] == 8


def test_recovery_admission(tmp_path: Path) -> None:
    """A bounded unterminated suffix is ignored without changing accepted state."""
    a = Archive(stream="case-e", max_tail=40)
    a.event(9, b"sealed")
    a.checkpoint()
    tail = b"E\t3\t0\t10\t6162"
    result, report = invoke(tmp_path, a, log=a.log(tail))
    assert result.returncode == 0
    assert report["recovered_tail_bytes"] == len(tail)
    assert report["last_sequence"] == 2
    assert report["chain_hash"] == a.chain.hex()


def test_canonical_encoding_denial(tmp_path: Path) -> None:
    """Canonical hexadecimal is mandatory even when decoded bytes would match."""
    a = Archive(stream="case-f")
    a.event(1, b"AZ", payload_text="415A")
    a.checkpoint()
    report = assert_denied(tmp_path, a, "FORMAT")
    assert report["committed_records"] == 0


@pytest.mark.parametrize(
    ("variant", "reason"),
    [
        ("sequence", "SEQUENCE"),
        ("epoch", "EPOCH"),
        ("previous", "PREVIOUS"),
        ("auth", "AUTH"),
    ],
)
def test_record_control_denials(tmp_path: Path, variant: str, reason: str) -> None:
    """Authenticated record framing rejects altered chain-control fields by precedence."""
    a = Archive(stream=f"case-g-{variant}")
    a.event(1, b"entry")
    line = a.lines[0].split("\t")
    if variant == "sequence":
        line[1] = "2"
    elif variant == "epoch":
        line[2] = "1"
    elif variant == "previous":
        line[-2] = "00" * 32
    else:
        line[-1] = line[-1][:-1] + ("0" if line[-1][-1] != "0" else "1")
    bad = ("\t".join(line) + "\n").encode()
    assert_denied(tmp_path, a, reason, log=bad)


def test_validation_precedence(tmp_path: Path) -> None:
    """Simultaneous faults select the earliest applicable denial reason."""
    basic_cases = [
        ("format-sequence", "02", "1", "00" * 32, True, "FORMAT"),
        ("sequence-epoch", "2", "1", None, False, "SEQUENCE"),
        ("epoch-previous", "1", "1", "00" * 32, False, "EPOCH"),
        ("previous-auth", "1", "0", "00" * 32, True, "PREVIOUS"),
    ]
    for name, seq, epoch, previous, break_mac, reason in basic_cases:
        archive = Archive(stream=f"precedence-{name}")
        archive.event(1, b"entry")
        line = archive.lines[0].split("\t")
        line[1] = seq
        line[2] = epoch
        if previous is not None:
            line[-2] = previous
        if break_mac:
            line[-1] = ("0" if line[-1][0] != "0" else "1") + line[-1][1:]
        assert_denied(tmp_path / name, archive, reason, log=("\t".join(line) + "\n").encode())

    timestamp = Archive(stream="precedence-auth-timestamp")
    timestamp.event(10, b"first")
    timestamp.event(9, b"second")
    timestamp_line = timestamp.lines[1].split("\t")
    timestamp_line[-1] = ("0" if timestamp_line[-1][0] != "0" else "1") + timestamp_line[-1][1:]
    report = assert_denied(
        tmp_path / "auth-timestamp",
        timestamp,
        "AUTH",
        log=(timestamp.lines[0] + "\n" + "\t".join(timestamp_line) + "\n").encode(),
    )
    assert report["committed_records"] == 1

    rotation = Archive(stream="precedence-auth-rotation")
    rotation.event(1, b"first")
    rotation.rotate(commitment=bytes(32))
    rotation_line = rotation.lines[1].split("\t")
    rotation_line[-1] = ("0" if rotation_line[-1][0] != "0" else "1") + rotation_line[-1][1:]
    assert_denied(
        tmp_path / "auth-rotation",
        rotation,
        "AUTH",
        log=(rotation.lines[0] + "\n" + "\t".join(rotation_line) + "\n").encode(),
    )

    checkpoint = Archive(stream="precedence-auth-checkpoint")
    checkpoint.event(1, b"first")
    checkpoint.checkpoint(root=b"\xff" * 32)
    checkpoint_line = checkpoint.lines[1].split("\t")
    checkpoint_line[-1] = ("0" if checkpoint_line[-1][0] != "0" else "1") + checkpoint_line[-1][1:]
    assert_denied(
        tmp_path / "auth-checkpoint",
        checkpoint,
        "AUTH",
        log=(checkpoint.lines[0] + "\n" + "\t".join(checkpoint_line) + "\n").encode(),
    )

    tail = Archive(stream="precedence-tail-unsealed", max_tail=0)
    tail.event(1, b"open")
    assert_denied(tmp_path / "tail-unsealed", tail, "TAIL", log=tail.log(b"x"))

    policy = Archive(stream="precedence-policy")
    assert_denied(
        tmp_path / "policy-format",
        policy,
        "POLICY",
        log=b"bad\n",
        policy=policy.policy().replace(b"FSPOLICY\t1", b"FSPOLICY\t2"),
    )


def test_event_order_denial(tmp_path: Path) -> None:
    """Event timestamps are monotone across intervening non-event records."""
    a = Archive(stream="case-h")
    a.event(8, b"first")
    a.rotate()
    a.event(7, b"second")
    a.checkpoint()
    report = assert_denied(tmp_path, a, "TIMESTAMP")
    assert report["committed_records"] == 2
    assert report["epoch"] == 1


def test_proof_denials(tmp_path: Path) -> None:
    """Epoch commitments and checkpoint roots must match policy-backed evidence."""
    a = Archive(stream="case-i")
    a.event(1, b"first")
    a.rotate(commitment=b"\x00" * 32)
    assert_denied(tmp_path, a, "ROTATION")

    skipped = Archive(stream="case-i-epoch")
    skipped.seq = 1
    commitment = sha(b"FLOG-KEY\0" + (2).to_bytes(8, "big") + skipped.keys[2])
    skipped._append(f"R\t1\t0\t2\t{commitment.hex()}", 0, True)
    assert_denied(tmp_path / "epoch", skipped, "ROTATION")

    b = Archive(stream="case-i-root")
    b.event(1, b"first")
    b.checkpoint(root=b"\xff" * 32)
    assert_denied(tmp_path / "root", b, "CHECKPOINT")

    for index, coverage in enumerate([(0, 1, 1), (1, 2, 1), (1, 1, 2)]):
        malformed = Archive(stream=f"case-i-coverage-{index}")
        malformed.event(1, b"first")
        accepted_chain = malformed.chain
        malformed.seq += 1
        root = merkle([leaf[1] for leaf in malformed.leaves])
        first, last, count = coverage
        malformed._append(
            f"C\t2\t0\t{first}\t{last}\t{count}\t{root.hex()}", 0, False
        )
        report = assert_denied(tmp_path / f"coverage-{index}", malformed, "CHECKPOINT")
        assert report["committed_records"] == 1
        assert report["last_sequence"] == 1
        assert report["chain_hash"] == accepted_chain.hex()


def test_sealing_denial(tmp_path: Path) -> None:
    """Only a checkpoint-sealed, nonempty committed prefix can be admitted."""
    a = Archive(stream="case-j")
    a.event(1, b"open")
    assert_denied(tmp_path / "open", a, "UNSEALED")
    b = Archive(stream="case-j-empty")
    report = assert_denied(tmp_path / "empty", b, "UNSEALED")
    assert report == OrderedDict(
        admitted=False,
        reason="UNSEALED",
        stream=b.stream,
        committed_records=0,
        last_sequence=0,
        epoch=0,
        chain_hash=b.genesis.hex(),
        last_checkpoint=0,
        recovered_tail_bytes=0,
    )

    empty_checkpoint = Archive(stream="case-j-checkpoint")
    empty_checkpoint.seq = 1
    empty_checkpoint._append(f"C\t1\t0\t0\t0\t0\t{bytes(32).hex()}", 0, False)
    report = assert_denied(tmp_path / "checkpoint", empty_checkpoint, "CHECKPOINT")
    assert report == OrderedDict(
        admitted=False,
        reason="CHECKPOINT",
        stream=empty_checkpoint.stream,
        committed_records=0,
        last_sequence=0,
        epoch=0,
        chain_hash=empty_checkpoint.genesis.hex(),
        last_checkpoint=0,
        recovered_tail_bytes=0,
    )


def test_tail_denials(tmp_path: Path) -> None:
    """Committed corruption and excessive suffixes remain distinct denials."""
    a = Archive(stream="case-k", max_tail=4)
    a.event(1, b"good")
    a.checkpoint()
    assert_denied(tmp_path / "committed", a, "FORMAT", log=a.log() + b"bad\n")
    report = assert_denied(tmp_path / "tail", a, "TAIL", log=a.log(b"12345"))
    assert report["recovered_tail_bytes"] == 5
    assert report["last_checkpoint"] == 2


def test_policy_report(tmp_path: Path) -> None:
    """Policy syntax is exact and policy denial still emits the report schema."""
    a = Archive(stream="case-l")
    a.event(1, b"entry")
    a.checkpoint()
    malformed = a.policy().replace(b"KEY\t0", b"KEY\t00", 1)
    report = assert_denied(tmp_path, a, "POLICY", policy=malformed)
    assert report == OrderedDict(
        admitted=False,
        reason="POLICY",
        stream="",
        committed_records=0,
        last_sequence=0,
        epoch=0,
        chain_hash="00" * 32,
        last_checkpoint=0,
        recovered_tail_bytes=0,
    )


def test_policy_denials(tmp_path: Path) -> None:
    """Independent malformed policy classes all receive policy denial."""
    a = Archive(stream="case-o")
    a.event(1, b"entry")
    a.checkpoint()
    valid = a.policy()
    genesis = a.genesis.hex().encode()
    policy_lines = valid.decode().splitlines()
    excessive_keys = policy_lines[:4]
    excessive_keys.extend(
        f"KEY\t{epoch}\t{sha(f'excess-key-{epoch}'.encode()).hex()}" for epoch in range(33)
    )
    excessive_keys.append("END")
    variants = [
        valid[:-1],
        valid.replace(b"STREAM\tcase-o", b"STREAM\t"),
        valid.replace(b"STREAM\tcase-o", b"STREAM\t" + b"s" * 33),
        valid.replace(b"STREAM\tcase-o", b"STREAM\tCase-o"),
        valid.replace(b"MAX_TAIL\t64", b"MAX_TAIL\t4097"),
        valid.replace(b"KEY\t0", b"KEY\t1", 1),
        valid.replace(genesis, genesis.upper(), 1),
        valid.replace(b"END\n", b"END\r\n"),
        valid.replace(b"GENESIS", b"MAX_TAIL", 1),
        b"\n".join(line for line in valid.splitlines() if not line.startswith(b"KEY\t"))
        + b"\n",
        valid + b"EXTRA\n",
        ("\n".join(excessive_keys) + "\n").encode(),
    ]
    for index, policy in enumerate(variants):
        assert_denied(tmp_path / str(index), a, "POLICY", policy=policy)


def test_record_format_denials(tmp_path: Path) -> None:
    """Committed line encoding, field ranges, and length limits are enforced."""
    a = Archive(stream="case-p")
    a.event(1, b"entry")
    fields = a.lines[0].split("\t")
    variants = []
    changed = fields.copy()
    changed[1] = "01"
    variants.append(("\t".join(changed) + "\n").encode())
    changed = fields.copy()
    changed[3] = "9223372036854775808"
    variants.append(("\t".join(changed) + "\n").encode())
    changed = fields.copy()
    changed[4] = ""
    variants.append(("\t".join(changed) + "\n").encode())
    changed = fields.copy()
    changed[4] = "aa" * 513
    variants.append(("\t".join(changed) + "\n").encode())
    variants.extend(
        [
            (a.lines[0] + "\r\n").encode(),
            a.lines[0].encode() + b"\x00\n",
            a.lines[0].encode() + b"\x80\n",
            b"E" + b"x" * 4096 + b"\n",
        ]
    )
    for index, log in enumerate(variants):
        assert_denied(tmp_path / str(index), a, "FORMAT", log=log)


def test_invocation_denials(tmp_path: Path) -> None:
    """Invalid invocation and aliased paths return usage status without a report."""
    missing = tmp_path / "missing-report.json"
    result = subprocess.run(
        [str(PROGRAM), "--report", str(missing)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 64 and not missing.exists()

    a = Archive(stream="case-m")
    policy = tmp_path / "same"
    log = tmp_path / "log"
    policy.write_bytes(a.policy())
    original = policy.read_bytes()
    log.write_bytes(b"")
    result = subprocess.run(
        [str(PROGRAM), "--policy", str(policy), "--log", str(log), "--report", str(policy)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 64
    assert policy.read_bytes() == original


def test_path_limit_denials(tmp_path: Path) -> None:
    """File limits and inaccessible path forms fail as usage without report damage."""
    a = Archive(stream="case-q")
    policy = tmp_path / "policy"
    log = tmp_path / "log"
    report = tmp_path / "report"
    policy.write_bytes(a.policy())
    log.write_bytes(b"x" * (1024 * 1024 + 1))
    report.write_text("keep\n")
    command = [
        str(PROGRAM),
        "--policy",
        str(policy),
        "--log",
        str(log),
        "--report",
        str(report),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert result.stdout == "" and result.stderr == ""
    assert report.read_text() == "keep\n"

    policy.write_bytes(b"x" * (64 * 1024 + 1))
    log.write_bytes(b"")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert report.read_text() == "keep\n"
    policy.write_bytes(a.policy())

    missing = tmp_path / "missing"
    command[4] = str(missing)
    command[6] = str(tmp_path / "new-report")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert not Path(command[6]).exists()

    log_directory = tmp_path / "log-directory"
    log_directory.mkdir()
    command[4] = str(log_directory)
    command[6] = str(tmp_path / "directory-input-report")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert not Path(command[6]).exists()

    directory_report = tmp_path / "report-dir"
    directory_report.mkdir()
    log.write_bytes(b"")
    command[4] = str(log)
    command[6] = str(directory_report)
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert directory_report.is_dir() and not list(directory_report.iterdir())

    command[4] = str(policy)
    command[6] = str(tmp_path / "alias-report")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert not Path(command[6]).exists()


def test_generated_archive_admission(tmp_path: Path) -> None:
    """A generated multi-epoch archive has deterministic cross-artifact state."""
    a = Archive(stream="case-n", key_count=4)
    timestamp = 1000
    for epoch in range(3):
        for item in range(epoch + 2):
            a.event(timestamp, sha(f"{epoch}:{item}".encode())[: 3 + item])
            timestamp += item
        a.checkpoint()
        if epoch != 2:
            a.rotate()
    a.event(timestamp + 1, b"closing")
    a.checkpoint()
    result, report = invoke(tmp_path, a)
    assert result.returncode == 0
    assert report["chain_hash"] == a.chain.hex()
    assert report["epoch"] == 2
    assert report["last_checkpoint"] == a.seq
