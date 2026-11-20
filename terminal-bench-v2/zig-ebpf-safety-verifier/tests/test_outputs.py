import json
import subprocess
from pathlib import Path

ROOT = Path("/opt/ebpf-admit")
CHECK = ROOT / "scripts" / "check.sh"


def run_gate(tmp_path, name, program):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(program, separators=(",", ":")) + "\n")
    completed = subprocess.run(
        [str(CHECK), str(path)],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert len(lines) == 1
    parts = lines[0].split(" ")
    assert len(parts) == 2
    assert parts[0] in {"ALLOW", "DENY"}
    assert parts[1]
    return tuple(parts)


def prog(pkt_len, insns):
    return {"pkt_len": pkt_len, "insns": insns}


def test_security_policy_samples(tmp_path):
    """Bundled samples keep stable ALLOW/DENY diagnostics through check.sh."""
    samples = [
        (ROOT / "cases" / "minimal.json", ("ALLOW", "OK")),
        (ROOT / "cases" / "stack.json", ("ALLOW", "OK")),
        (ROOT / "cases" / "reject.json", ("DENY", "BAD_PACKET")),
    ]
    before = {sample: sample.read_bytes() for sample, _ in samples}
    for sample, expected in samples:
        completed = subprocess.run(
            [str(CHECK), str(sample)],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == f"{expected[0]} {expected[1]}\n"
    assert {sample: sample.read_bytes() for sample, _ in samples} == before


def test_security_policy_stack_paths(tmp_path):
    """Stack joins require each loaded byte to be written on every feasible path."""
    program = prog(
        32,
        [
            {"op": "call", "helper": 1},
            {"op": "jge_imm", "dst": 0, "imm": 1, "target": 4},
            {"op": "st_stack", "off": -8, "size": 1},
            {"op": "ja", "target": 5},
            {"op": "st_stack", "off": -7, "size": 1},
            {"op": "ld_stack", "dst": 2, "off": -8, "size": 2},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_b", program) == ("DENY", "BAD_STACK")


def test_security_policy_helper_state(tmp_path):
    """Helper calls clobber caller-saved registers before later packet access."""
    unsafe = prog(
        16,
        [
            {"op": "call", "helper": 1},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 4},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_c", unsafe) == ("DENY", "BAD_REG")

    for reg in range(6, 11):
        preserved = prog(
            16,
            [
                {"op": "mov_reg", "dst": reg, "src": 1},
                {"op": "call", "helper": 1},
                {"op": "ld_pkt", "dst": 2, "base": reg, "off": 4, "size": 4},
                {"op": "exit"},
            ],
        )
        assert run_gate(tmp_path, f"case_c_preserved_r{reg}", preserved) == ("ALLOW", "OK")


def test_security_policy_packet_bounds(tmp_path):
    """Packet boundary checks reject negative and one-past-end access ranges."""
    for idx, (offset, size, expected) in enumerate(
        [(0, 1, "ALLOW"), (7, 1, "ALLOW"), (8, 1, "DENY"), (-1, 1, "DENY"), (4, 4, "ALLOW"), (5, 4, "DENY")]
    ):
        program = prog(
            8,
            [
                {"op": "add_imm", "dst": 1, "imm": offset},
                {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": size},
                {"op": "exit"},
            ],
        )
        decision, code = run_gate(tmp_path, f"case_d_{idx}", program)
        assert decision == expected
        if decision == "DENY":
            assert code == "BAD_PACKET"


def test_security_policy_arithmetic_branches(tmp_path):
    """ALU32 truncation and signed branches preserve valid packet offsets."""
    programs = [
        prog(
            24,
            [
                {"op": "mov_imm", "dst": 0, "imm": -4294967296},
                {"op": "alu32_add_imm", "dst": 0, "imm": 12},
                {"op": "add_reg", "dst": 1, "src": 0},
                {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 4},
                {"op": "exit"},
            ],
        ),
        prog(
            8,
            [
                {"op": "mov_imm", "dst": 0, "imm": -1},
                {"op": "jsge_imm", "dst": 0, "imm": 0, "target": 4},
                {"op": "exit"},
                {"op": "exit"},
                {"op": "add_imm", "dst": 1, "imm": -1},
                {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 1},
                {"op": "exit"},
            ],
        ),
    ]
    for idx, program in enumerate(programs):
        assert run_gate(tmp_path, f"case_e_{idx}", program) == ("ALLOW", "OK")


def test_security_policy_alu32_exact_low_bits_survive_high_bounds(tmp_path):
    """ALU32 arithmetic must keep exact low bits even when high u64 bits are set."""
    program = prog(
        40,
        [
            {"op": "mov_imm", "dst": 0, "imm": -8589934592},
            {"op": "alu32_add_imm", "dst": 0, "imm": 20},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 3, "base": 1, "off": 0, "size": 8},
            {"op": "alu32_add_imm", "dst": 0, "imm": 12},
            {"op": "mov_reg", "dst": 4, "src": 0},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_e_exact_low", program) == ("ALLOW", "OK")


def test_security_policy_control_flow_targets(tmp_path):
    """Every feasible branch target must be a valid instruction index."""
    program = prog(
        16,
        [
            {"op": "call", "helper": 1},
            {"op": "jge_imm", "dst": 0, "imm": 1, "target": 99},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_f", program) == ("DENY", "BAD_JUMP")

    negative = prog(16, [{"op": "ja", "target": -1}])
    assert run_gate(tmp_path, "case_f_negative", negative) == ("DENY", "BAD_JUMP")


def test_security_policy_input_schema(tmp_path):
    """Malformed row schemas produce stable INPUT denial codes."""
    missing_target = prog(16, [{"op": "jge_imm", "dst": 0, "imm": 0}, {"op": "exit"}])
    assert run_gate(tmp_path, "case_schema", missing_target) == ("DENY", "INPUT")

    missing_unconditional_target = prog(16, [{"op": "ja"}, {"op": "exit"}])
    assert run_gate(tmp_path, "case_schema_ja", missing_unconditional_target) == ("DENY", "INPUT")

    typed_bad_jump = prog(16, [{"op": "jge_imm", "dst": 0, "imm": 0, "target": "4"}, {"op": "exit"}])
    assert run_gate(tmp_path, "case_schema_typed_jump", typed_bad_jump) == ("DENY", "INPUT")

    empty_insns = prog(16, [])
    assert run_gate(tmp_path, "case_schema_empty", empty_insns) == ("DENY", "INPUT")


def test_security_policy_input_schema_edges_and_stack_slab(tmp_path):
    """Admission-record boundaries and stack slab limits use the documented denial codes."""
    cases = [
        ("unknown_op", prog(16, [{"op": "xor_imm", "dst": 0, "imm": 1}, {"op": "exit"}]), ("DENY", "INPUT")),
        ("negative_pkt_len", prog(-1, [{"op": "exit"}]), ("DENY", "INPUT")),
        ("bad_size", prog(16, [{"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 3}, {"op": "exit"}]), ("DENY", "INPUT")),
        ("bad_register", prog(16, [{"op": "mov_imm", "dst": 11, "imm": 0}, {"op": "exit"}]), ("DENY", "INPUT")),
        ("bad_source_register", prog(16, [{"op": "mov_reg", "dst": 0, "src": -1}, {"op": "exit"}]), ("DENY", "INPUT")),
        ("stack_below_slab", prog(16, [{"op": "st_stack", "off": -520, "size": 8}, {"op": "exit"}]), ("DENY", "BAD_STACK")),
        ("stack_above_slab", prog(16, [{"op": "st_stack", "off": -1, "size": 8}, {"op": "exit"}]), ("DENY", "BAD_STACK")),
    ]
    for name, program, expected in cases:
        assert run_gate(tmp_path, f"case_schema_edge_{name}", program) == expected


def test_security_policy_bounded_offsets(tmp_path):
    """Bounded low-32 ALU results remain precise enough for packet safety."""
    program = prog(
        32,
        [
            {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 1},
            {"op": "jge_imm", "dst": 0, "imm": 16, "target": 6},
            {"op": "alu32_add_imm", "dst": 0, "imm": 8},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 1},
            {"op": "exit"},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_g", program) == ("ALLOW", "OK")


def test_security_policy_generated_bounded_offset_matrix(tmp_path):
    """Generated packet-offset admission probes require semantic safety rulings."""
    sizes = [1, 2, 4, 8, 1]
    for idx, pkt_len in enumerate([24, 29, 37, 46, 58]):
        threshold = 7 + idx
        add = 2 + idx
        size = sizes[idx]
        safe = prog(
            pkt_len,
            [
                {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 1},
                {"op": "jge_imm", "dst": 0, "imm": threshold, "target": 6},
                {"op": "alu32_add_imm", "dst": 0, "imm": add},
                {"op": "add_reg", "dst": 1, "src": 0},
                {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": size},
                {"op": "exit"},
                {"op": "exit"},
            ],
        )
        assert run_gate(tmp_path, f"case_g_matrix_safe_{idx}", safe) == ("ALLOW", "OK")

        unsafe = prog(
            pkt_len,
            [
                {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 1},
                {"op": "jge_imm", "dst": 0, "imm": threshold, "target": 6},
                {"op": "alu32_add_imm", "dst": 0, "imm": pkt_len - threshold + 2},
                {"op": "add_reg", "dst": 1, "src": 0},
                {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": size},
                {"op": "exit"},
                {"op": "exit"},
            ],
        )
        assert run_gate(tmp_path, f"case_g_matrix_unsafe_{idx}", unsafe) == ("DENY", "BAD_PACKET")


def test_security_policy_scalar_add_reg_offsets_and_loop(tmp_path):
    """Scalar register sums feed packet-offset proofs, while unstable loops are bounded."""
    exact_sum = prog(
        32,
        [
            {"op": "mov_imm", "dst": 0, "imm": 7},
            {"op": "mov_imm", "dst": 2, "imm": 5},
            {"op": "add_reg", "dst": 0, "src": 2},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 3, "base": 1, "off": 0, "size": 4},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_h_exact", exact_sum) == ("ALLOW", "OK")

    bounded_sum = prog(
        32,
        [
            {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 1},
            {"op": "jge_imm", "dst": 0, "imm": 16, "target": 7},
            {"op": "mov_imm", "dst": 2, "imm": 8},
            {"op": "add_reg", "dst": 0, "src": 2},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 3, "base": 1, "off": 0, "size": 8},
            {"op": "exit"},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_h_bounded", bounded_sum) == ("ALLOW", "OK")

    nonconverging = prog(
        8,
        [
            {"op": "mov_imm", "dst": 0, "imm": 0},
            {"op": "add_imm", "dst": 0, "imm": 1},
            {"op": "ja", "target": 1},
        ],
    )
    assert run_gate(tmp_path, "case_h_loop", nonconverging) == ("DENY", "LOOP")


def test_security_policy_deep_scalar_precision_and_overflow_guards(tmp_path):
    """Exact low-32 results and overflowing scalar joins cannot be confused with safe packet offsets."""
    exact_after_join = prog(
        64,
        [
            {"op": "mov_imm", "dst": 0, "imm": -4294967296},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 1},
            {"op": "jge_imm", "dst": 2, "imm": 128, "target": 5},
            {"op": "alu32_add_imm", "dst": 0, "imm": 24},
            {"op": "ja", "target": 6},
            {"op": "alu32_add_imm", "dst": 0, "imm": 24},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 8},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_i_exact_join", exact_after_join) == ("ALLOW", "OK")

    overflowing_sum = prog(
        32,
        [
            {"op": "mov_imm", "dst": 0, "imm": 9223372036854775807},
            {"op": "mov_imm", "dst": 2, "imm": 1},
            {"op": "add_reg", "dst": 0, "src": 2},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 3, "base": 1, "off": 0, "size": 1},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_i_overflow", overflowing_sum) == ("DENY", "BAD_REG")


def test_security_policy_packet_byte_masks_and_shifts(tmp_path):
    """Packet-byte ranges, bit masks, and shifts combine into bounded packet-offset proofs."""
    safe = prog(
        18,
        [
            {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 1},
            {"op": "and_imm", "dst": 0, "imm": 7},
            {"op": "lsh_imm", "dst": 0, "imm": 1},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 2},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_j_mask_shift_safe", safe) == ("ALLOW", "OK")

    unsafe = prog(
        18,
        [
            {"op": "ld_pkt", "dst": 0, "base": 1, "off": 0, "size": 1},
            {"op": "and_imm", "dst": 0, "imm": 15},
            {"op": "lsh_imm", "dst": 0, "imm": 1},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 2},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_j_mask_shift_unsafe", unsafe) == ("DENY", "BAD_PACKET")


def test_security_policy_equality_pruning_and_logical_shift(tmp_path):
    """Equality branches and logical right shifts must prune infeasible packet-offset paths."""
    equality_pruned = prog(
        16,
        [
            {"op": "mov_imm", "dst": 0, "imm": 2},
            {"op": "jeq_imm", "dst": 0, "imm": 2, "target": 5},
            {"op": "add_imm", "dst": 1, "imm": 99},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 1},
            {"op": "exit"},
            {"op": "add_imm", "dst": 1, "imm": 8},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 8},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_k_jeq_pruned", equality_pruned) == ("ALLOW", "OK")

    shifted = prog(
        32,
        [
            {"op": "mov_imm", "dst": 0, "imm": 120},
            {"op": "rsh_imm", "dst": 0, "imm": 3},
            {"op": "add_reg", "dst": 1, "src": 0},
            {"op": "ld_pkt", "dst": 2, "base": 1, "off": 0, "size": 8},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_k_rshift_exact", shifted) == ("ALLOW", "OK")


def test_security_policy_input_precedence_for_whole_program_schema(tmp_path):
    """Malformed later instruction records are INPUT before earlier semantic register errors."""
    malformed_after_bad_reg = prog(
        16,
        [
            {"op": "mov_reg", "dst": 0, "src": 2},
            {"op": "and_imm", "dst": 0},
            {"op": "exit"},
        ],
    )
    assert run_gate(tmp_path, "case_l_schema_precedes_semantics", malformed_after_bad_reg) == ("DENY", "INPUT")

    invalid_shift = prog(16, [{"op": "lsh_imm", "dst": 0, "imm": 64}, {"op": "exit"}])
    assert run_gate(tmp_path, "case_l_invalid_shift", invalid_shift) == ("DENY", "INPUT")
