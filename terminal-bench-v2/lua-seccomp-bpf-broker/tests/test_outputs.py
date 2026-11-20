import json
import shutil
import subprocess
import textwrap
from collections import Counter
from pathlib import Path


def run_gate(src: Path, out: Path):
    if out.exists():
        shutil.rmtree(out)
    result = subprocess.run(
        ["lua5.4", "/app/policy_gate.lua", str(src), str(out)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    audit_path = out / "audit.json"
    findings_path = out / "findings.txt"
    assert audit_path.exists()
    assert findings_path.exists()
    return json.loads(audit_path.read_text()), findings_path.read_text()


def write_policy(root: Path, name: str, body: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(textwrap.dedent(body))


def by_id(report):
    return {p["id"]: p for p in report["policies"]}


def test_default_policies_emit_stable_security_artifacts(tmp_path):
    """The bundled security audit must emit deterministic JSON and line-oriented findings."""
    out = tmp_path / "out"
    first, findings = run_gate(Path("/app/policies"), out)
    second, findings2 = run_gate(Path("/app/policies"), out)
    assert first == second
    assert findings == findings2
    assert list(by_id(first)) == sorted(by_id(first))
    assert {"id", "status", "stats", "decisions", "findings"} <= set(first["policies"][0])
    assert "bad-wire|critical|MALFORMED_PROGRAM|" in findings
    assert by_id(first)["broker-core"]["stats"] == {"allow": 1, "deny": 2, "trap": 1}
    assert by_id(first)["io-edges"]["stats"] == {"allow": 2, "deny": 1, "trap": 0}


def test_bpf_jump_arch_and_x32_boundaries_are_enforced(tmp_path):
    """Control-flow decisions must honor BPF jump offsets and ABI separation."""
    policies = tmp_path / "policies"
    write_policy(
        policies,
        "abi.lua",
        """
        return {
          id = "abi",
          default_action = "KILL",
          program = {
            { op = "LD_ARCH" },
            { op = "JEQ", k = "AUDIT_ARCH_X86_64", jt = 1, jf = 0 },
            { op = "RET", action = "KILL" },
            { op = "LD_NR" },
            { op = "JSET", mask = 1073741824, jt = 0, jf = 1 },
            { op = "RET", action = "KILL" },
            { op = "JEQ", k = 257, jt = 0, jf = 1 },
            { op = "RET", action = "ALLOW" },
            { op = "RET", action = "KILL" }
          },
          syscalls = { { name = "openat", action = "ALLOW", args = {} } },
          samples = {
            { arch = "AUDIT_ARCH_X86_64", nr = "openat", args = {} },
            { arch = "AUDIT_ARCH_I386", nr = "openat", args = {} },
            { arch = "AUDIT_ARCH_X86_64", nr = 1073742081, args = {} }
          }
        }
        """,
    )
    report, findings = run_gate(policies, tmp_path / "out")
    decisions = by_id(report)["abi"]["decisions"]
    assert [d["action"] for d in decisions] == ["ALLOW", "KILL", "KILL"]
    assert findings == ""


def test_argument_predicates_and_precedence_match_policy_contract(tmp_path):
    """Argument masks, ranges, and action priority must agree with the policy model."""
    policies = tmp_path / "policies"
    write_policy(
        policies,
        "args.lua",
        """
        return {
          id = "args",
          default_action = "ERRNO",
          program = {
            { op = "LD_ARCH" },
            { op = "JEQ", k = "AUDIT_ARCH_X86_64", jt = 1, jf = 0 },
            { op = "RET", action = "KILL" },
            { op = "LD_NR" },
            { op = "JEQ", k = 1, jt = 0, jf = 1 },
            { op = "RET", action = "ALLOW" },
            { op = "RET", action = "ERRNO" }
          },
          syscalls = {
            { name = "write", action = "ALLOW", args = { { index = 0, op = "range", min = 3, max = 4 }, { index = 2, op = "range", min = 1, max = 4096 } } },
            { name = "openat", action = "ALLOW", args = { { index = 2, op = "masked_eq", mask = 2097155, value = 0 } } }
          },
          samples = {
            { arch = "AUDIT_ARCH_X86_64", nr = "write", args = { 2, 0, 4096 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "write", args = { 2, 0, 9000 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "openat", args = { -100, 0, 0 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "openat", args = { -100, 0, 3 } }
          }
        }
        """,
    )
    report, findings = run_gate(policies, tmp_path / "out")
    policy = by_id(report)["args"]
    assert policy["stats"] == {"allow": 2, "deny": 2, "trap": 0}
    assert [d["action"] for d in policy["decisions"]] == ["ALLOW", "ALLOW", "ERRNO", "ERRNO"]
    mismatch_lines = [line for line in findings.splitlines() if "DECISION_MISMATCH" in line]
    assert Counter(mismatch_lines) == Counter(
        {
            "args|high|DECISION_MISMATCH|write resolved to ALLOW not ERRNO": 2,
            "args|high|DECISION_MISMATCH|openat resolved to ERRNO not ALLOW": 1,
        }
    )


def test_arg_loads_unconditional_jumps_and_comparisons_drive_decisions(tmp_path):
    """Accumulator loads from syscall args and relative jumps must compose correctly."""
    policies = tmp_path / "policies"
    write_policy(
        policies,
        "arg_flow.lua",
        """
        return {
          id = "arg-flow",
          default_action = "KILL",
          program = {
            { op = "LD_ARCH" },
            { op = "JEQ", k = "AUDIT_ARCH_X86_64", jt = 1, jf = 0 },
            { op = "RET", action = "KILL" },
            { op = "LD_NR" },
            { op = "JEQ", k = 44, jt = 0, jf = 8 },
            { op = "LD_ARG", index = 5 },
            { op = "JSET", mask = 1, jt = 5, jf = 0 },
            { op = "LD_ARG", index = 2 },
            { op = "JGE", k = 4097, jt = 3, jf = 0 },
            { op = "JA", k = 1 },
            { op = "RET", action = "TRACE" },
            { op = "RET", action = "ALLOW" },
            { op = "RET", action = "KILL" },
            { op = "RET", action = "KILL" }
          },
          syscalls = {
            { name = "sendto", action = "ALLOW", args = { { index = 2, op = "range", min = 1, max = 4096 }, { index = 5, op = "eq", value = 0 } } }
          },
          samples = {
            { arch = "AUDIT_ARCH_X86_64", nr = "sendto", args = { 3, 8192, 512, 0, 0, 0 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "sendto", args = { 3, 8192, 512 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "sendto", args = { 3, 8192, 4097, 0, 0, 0 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "sendto", args = { 3, 8192, 128, 0, 0, 1 } },
            { arch = "AUDIT_ARCH_X86_64", nr = "close", args = { 3 } }
          }
        }
        """,
    )
    report, findings = run_gate(policies, tmp_path / "out")
    policy = by_id(report)["arg-flow"]
    assert [d["action"] for d in policy["decisions"]] == ["ALLOW", "ALLOW", "KILL", "KILL", "KILL"]
    assert policy["stats"] == {"allow": 2, "deny": 3, "trap": 0}
    assert findings == ""


def test_malformed_programs_do_not_mask_other_policy_results(tmp_path):
    """Invalid programs must be reported without making valid policies nondeterministic."""
    policies = tmp_path / "policies"
    write_policy(
        policies,
        "z_bad.lua",
        """
        return { id = "z-bad", default_action = "KILL", program = { { op = "LD_NR" }, { op = "RET" } }, syscalls = {}, samples = { { arch = "AUDIT_ARCH_X86_64", nr = "close", args = {} } } }
        """,
    )
    write_policy(
        policies,
        "a_good.lua",
        """
        return { id = "a-good", default_action = "KILL", program = { { op = "LD_NR" }, { op = "JEQ", k = 3, jt = 0, jf = 1 }, { op = "RET", action = "ALLOW" }, { op = "RET", action = "KILL" } }, syscalls = { { name = "close", action = "ALLOW", args = {} } }, samples = { { arch = "AUDIT_ARCH_X86_64", nr = "close", args = { 9 } } } }
        """,
    )
    report, findings = run_gate(policies, tmp_path / "out")
    assert [p["id"] for p in report["policies"]] == ["a-good", "z-bad"]
    assert by_id(report)["a-good"]["findings"] == []
    assert by_id(report)["z-bad"]["status"] == "invalid"
    assert "z-bad|critical|MALFORMED_PROGRAM|" in findings
