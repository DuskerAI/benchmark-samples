import json
import subprocess
from pathlib import Path

import pytest


APP = Path("/app/ebpfgate")
BIN = APP / "bin" / "ebpfgate"


@pytest.fixture(scope="session", autouse=True)
def build_gate():
    subprocess.run(["bash", str(APP / "scripts" / "build.sh")], cwd=APP, check=True)
    assert BIN.exists()


def write_case(root: Path, policy: str, programs: dict[str, str]):
    policy_path = root / "policy.txt"
    bundle = root / "bundle"
    bundle.mkdir()
    policy_path.write_text(policy, encoding="utf-8")
    for name, body in programs.items():
        (bundle / name).write_text(body.strip() + "\n", encoding="utf-8")
    return policy_path, bundle, root / "report.json"


def run_gate(policy: Path, bundle: Path, report: Path):
    return subprocess.run(
        [str(BIN), "--policy", str(policy), "--bundle", str(bundle), "--out", str(report)],
        cwd=APP,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def read_report(report: Path):
    assert report.exists(), "admission gate did not write the requested report"
    return json.loads(report.read_text(encoding="utf-8"))


def test_rejects_unsafe_admission_paths(tmp_path):
    """The gate denies unsafe bundles while still admitting the safe program."""
    policy, bundle, report = write_case(
        tmp_path,
        "\n".join([
            "max_tail=2",
            "helper=ktime_get_ns",
            "helper=map_lookup",
            "map=state:8:4",
            "map=small:4:2",
            "",
        ]),
        {
            "alpha.ebpf": """
                id alpha
                mov r0 1
                lookup r1 state r0
                jnull r1 done
                load r2 r1 0 8
                helper ktime_get_ns
                done:
                exit
            """,
            "bad_helper.ebpf": """
                id bad_helper
                helper map_lookup_extra
                exit
            """,
            "bad_key.ebpf": """
                id bad_key
                mov r0 3
                add r0 2
                lookup r1 state r0
                jnull r1 done
                load r2 r1 0 4
                done:
                exit
            """,
            "bad_nullable.ebpf": """
                id bad_nullable
                mov r0 1
                lookup r1 state r0
                load r2 r1 0 4
                exit
            """,
            "branch_window.ebpf": """
                id branch_window
                jgt r0 10 safe
                ptr r1 small
                load r2 r1 3 2
                exit
                safe:
                exit
            """,
            "loopa.ebpf": """
                id loopa
                tail loopb
            """,
            "loopb.ebpf": """
                id loopb
                tail loopa
            """,
        },
    )
    completed = run_gate(policy, bundle, report)
    assert completed.returncode != 0, completed.stderr
    data = read_report(report)
    assert data == {
        "accepted": ["alpha"],
        "rejected": [
            {"id": "bad_helper", "reason": "helper"},
            {"id": "bad_key", "reason": "memory"},
            {"id": "bad_nullable", "reason": "memory"},
            {"id": "branch_window", "reason": "memory"},
            {"id": "loopa", "reason": "tail-call"},
            {"id": "loopb", "reason": "tail-call"},
        ],
        "summary": {"accepted": 1, "rejected": 6},
    }


def test_accepts_precise_safe_bundle(tmp_path):
    """The gate accepts safe direct-pointer, tail-call, and branch-narrowed programs."""
    policy, bundle, report = write_case(
        tmp_path,
        "\n".join([
            "max_tail=3",
            "helper=ktime_get_ns",
            "map=state:8:255",
            "",
        ]),
        {
            "p1.ebpf": """
                id p1
                tail p2
            """,
            "p2.ebpf": """
                id p2
                ptr r1 state
                load r2 r1 4 4
                helper ktime_get_ns
                exit
            """,
            "gamma.ebpf": """
                id gamma
                mov r0 2
                lookup r1 state r0
                jnull r1 done
                load r2 r1 0 8
                done:
                exit
            """,
            "edge.ebpf": """
                id edge
                jeq r0 255 done
                lookup r1 state r0
                jnull r1 done
                load r2 r1 4 4
                done:
                exit
            """,
        },
    )
    completed = run_gate(policy, bundle, report)
    assert completed.returncode == 0, completed.stderr
    data = read_report(report)
    assert data == {
        "accepted": ["edge", "gamma", "p1", "p2"],
        "rejected": [],
        "summary": {"accepted": 4, "rejected": 0},
    }


def test_rejects_malformed_programs_with_format_reason(tmp_path):
    """Malformed files use the format reason and force a non-zero exit."""
    policy, bundle, report = write_case(
        tmp_path,
        "\n".join([
            "max_tail=1",
            "helper=ktime_get_ns",
            "map=state:8:4",
            "",
        ]),
        {
            "extra_operand.ebpf": """
                id extra_operand
                helper ktime_get_ns spare
                exit
            """,
            "late_id.ebpf": """
                mov r0 1
                id late_id
                exit
            """,
            "missing_operand.ebpf": """
                id missing_operand
                mov r0
                exit
            """,
        },
    )
    completed = run_gate(policy, bundle, report)
    assert completed.returncode != 0
    assert read_report(report) == {
        "accepted": [],
        "rejected": [
            {"id": "extra_operand", "reason": "format"},
            {"id": "late_id", "reason": "format"},
            {"id": "missing_operand", "reason": "format"},
        ],
        "summary": {"accepted": 0, "rejected": 3},
    }


def test_rejects_nonterminating_and_invalid_control_flow(tmp_path):
    """Nonterminating paths and instruction-pointer fallthroughs use control-flow."""
    policy, bundle, report = write_case(
        tmp_path,
        "\n".join([
            "max_tail=1",
            "helper=ktime_get_ns",
            "map=state:8:4",
            "",
        ]),
        {
            "falloff.ebpf": """
                id falloff
                mov r0 7
            """,
            "spin.ebpf": """
                id spin
                loop:
                jeq r0 0 loop
                exit
            """,
        },
    )
    completed = run_gate(policy, bundle, report)
    assert completed.returncode != 0
    assert read_report(report) == {
        "accepted": [],
        "rejected": [
            {"id": "falloff", "reason": "control-flow"},
            {"id": "spin", "reason": "control-flow"},
        ],
        "summary": {"accepted": 0, "rejected": 2},
    }


def test_exit_code_is_nonzero_when_any_program_is_rejected(tmp_path):
    """A mixed report is written, but the process fails if any id is rejected."""
    policy, bundle, report = write_case(
        tmp_path,
        "\n".join([
            "max_tail=1",
            "helper=ktime_get_ns",
            "map=state:8:4",
            "",
        ]),
        {
            "ok.ebpf": """
                id ok
                ptr r1 state
                load r2 r1 0 4
                exit
            """,
            "bad.ebpf": """
                id bad
                helper ktime_get_ns_extra
                exit
            """,
        },
    )
    completed = run_gate(policy, bundle, report)
    assert completed.returncode != 0
    assert read_report(report) == {
        "accepted": ["ok"],
        "rejected": [{"id": "bad", "reason": "helper"}],
        "summary": {"accepted": 1, "rejected": 1},
    }
