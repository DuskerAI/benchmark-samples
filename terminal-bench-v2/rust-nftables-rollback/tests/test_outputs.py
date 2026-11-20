import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

APP = Path("/app/firewall")
BIN = APP / "bin" / "fwplan"


@dataclass
class Ruleset:
    tables: set[tuple[str, str]] = field(default_factory=set)
    sets: dict[tuple[str, str, str], tuple[str, ...]] = field(default_factory=dict)
    chains: dict[tuple[str, str, str], tuple[str, int]] = field(default_factory=dict)
    rules: dict[tuple[str, str, str, int], tuple[str, ...]] = field(
        default_factory=dict
    )

    def clone(self):
        return Ruleset(
            set(self.tables), dict(self.sets), dict(self.chains), dict(self.rules)
        )


def clean_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def elems(token: str) -> tuple[str, ...]:
    if token == "-":
        return ()
    return tuple(sorted({part.strip() for part in token.split(",") if part.strip()}))


def expr(token: str) -> tuple[str, ...]:
    if token == "-":
        return ()
    return tuple(part.strip() for part in token.split(";") if part.strip())


def fmt_elems(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "-"


def fmt_expr(values: tuple[str, ...]) -> str:
    return ";".join(values) if values else "-"


def check_expr(
    rs: Ruleset, family: str, table: str, atoms: tuple[str, ...]
) -> str | None:
    for atom in atoms:
        if atom.startswith("set:") and (family, table, atom[4:]) not in rs.sets:
            return "missing-reference-set"
        if atom.startswith("jump:") and (family, table, atom[5:]) not in rs.chains:
            return "missing-jump"
        if atom.startswith("verdict:") and atom[8:] not in {
            "accept",
            "drop",
            "reject",
            "return",
        }:
            return "bad-verdict"
    return None


def parse_base(text: str) -> Ruleset:
    rs = Ruleset()
    pending_rules: list[tuple[str, str, str, int, tuple[str, ...]]] = []
    for line in clean_lines(text):
        parts = line.split()
        if parts[:1] == ["table"] and len(parts) == 3:
            key = (parts[1], parts[2])
            assert key not in rs.tables
            rs.tables.add(key)
        elif parts[:1] == ["set"] and len(parts) == 5:
            key = (parts[1], parts[2], parts[3])
            assert (parts[1], parts[2]) in rs.tables and key not in rs.sets
            rs.sets[key] = elems(parts[4])
        elif parts[:1] == ["chain"] and len(parts) == 6:
            key = (parts[1], parts[2], parts[3])
            prio = int(parts[5])
            assert (parts[1], parts[2]) in rs.tables and key not in rs.chains
            if parts[4] != "-":
                assert all(
                    (f, t) != (parts[1], parts[2])
                    or hook == "-"
                    or (hook, p) != (parts[4], prio)
                    for (f, t, _), (hook, p) in rs.chains.items()
                )
            rs.chains[key] = (parts[4], prio)
        elif parts[:1] == ["rule"] and len(parts) == 6:
            pending_rules.append(
                (parts[1], parts[2], parts[3], int(parts[4]), expr(parts[5]))
            )
        else:
            raise AssertionError(f"bad base line {line}")
    for family, table, chain, handle, atoms in pending_rules:
        key = (family, table, chain, handle)
        assert (family, table, chain) in rs.chains and key not in rs.rules
        assert check_expr(rs, family, table, atoms) is None
        rs.rules[key] = atoms
    return rs


def canonical_ruleset(rs: Ruleset) -> str:
    rows: list[str] = []
    for family, table in sorted(rs.tables):
        rows.append(f"table {family} {table}")
    for (family, table, name), values in sorted(rs.sets.items()):
        rows.append(f"set {family} {table} {name} {fmt_elems(values)}")
    for (family, table, name), (hook, prio) in sorted(rs.chains.items()):
        rows.append(f"chain {family} {table} {name} {hook} {prio}")
    for (family, table, chain, handle), atoms in sorted(rs.rules.items()):
        rows.append(f"rule {family} {table} {chain} {handle} {fmt_expr(atoms)}")
    return "\n".join(rows) + ("\n" if rows else "")


def apply_op(rs: Ruleset, line: str) -> tuple[str | None, str | None]:
    parts = line.split()
    if len(parts) < 2 or parts[0] not in {"add", "delete", "replace", "rename", "move"}:
        return "syntax", None
    act, noun = parts[0], parts[1]
    if noun == "table" and len(parts) == 4:
        key = (parts[2], parts[3])
        if act == "add":
            if key in rs.tables:
                return "duplicate-table", None
            rs.tables.add(key)
            return None, f"delete table {key[0]} {key[1]}"
        if (
            any(s[:2] == key for s in rs.sets)
            or any(c[:2] == key for c in rs.chains)
            or any(r[:2] == key for r in rs.rules)
        ):
            return "table-not-empty", None
        if key not in rs.tables:
            return "missing-table", None
        rs.tables.remove(key)
        return None, f"add table {key[0]} {key[1]}"
    if noun == "set" and len(parts) in {5, 6}:
        key = (parts[2], parts[3], parts[4])
        if act == "add" and len(parts) == 6:
            if key[:2] not in rs.tables:
                return "missing-table", None
            if key in rs.sets:
                return "duplicate-set", None
            values = elems(parts[5])
            rs.sets[key] = values
            return None, f"delete set {key[0]} {key[1]} {key[2]}"
        if act == "delete" and len(parts) == 5:
            if key not in rs.sets:
                return "missing-set", None
            if any(
                f == key[0] and t == key[1] and f"set:{key[2]}" in atoms
                for (f, t, _, _), atoms in rs.rules.items()
            ):
                return "set-in-use", None
            old = rs.sets.pop(key)
            return None, f"add set {key[0]} {key[1]} {key[2]} {fmt_elems(old)}"
        if act == "replace" and len(parts) == 6:
            if key not in rs.sets:
                return "missing-set", None
            old = rs.sets[key]
            rs.sets[key] = elems(parts[5])
            return None, f"replace set {key[0]} {key[1]} {key[2]} {fmt_elems(old)}"
    if noun == "chain" and len(parts) in {5, 7}:
        key = (parts[2], parts[3], parts[4])
        if act == "add" and len(parts) == 7:
            if key[:2] not in rs.tables:
                return "missing-table", None
            if key in rs.chains:
                return "duplicate-chain", None
            try:
                prio = int(parts[6])
            except ValueError:
                return "bad-priority", None
            if parts[5] != "-" and any(
                (f, t) == key[:2] and hook != "-" and (hook, p) == (parts[5], prio)
                for (f, t, _), (hook, p) in rs.chains.items()
            ):
                return "hook-collision", None
            rs.chains[key] = (parts[5], prio)
            return None, f"delete chain {key[0]} {key[1]} {key[2]}"
        if act == "delete" and len(parts) == 5:
            if key not in rs.chains:
                return "missing-chain", None
            if any(r[:3] == key for r in rs.rules):
                return "chain-not-empty", None
            if any(
                f == key[0] and t == key[1] and f"jump:{key[2]}" in atoms
                for (f, t, _, _), atoms in rs.rules.items()
            ):
                return "chain-referenced", None
            hook, prio = rs.chains.pop(key)
            return None, f"add chain {key[0]} {key[1]} {key[2]} {hook} {prio}"
    if act == "rename" and noun == "chain" and len(parts) == 6:
        old_key = (parts[2], parts[3], parts[4])
        new_key = (parts[2], parts[3], parts[5])
        if old_key not in rs.chains:
            return "missing-chain", None
        if new_key in rs.chains:
            return "duplicate-chain", None
        rs.chains[new_key] = rs.chains.pop(old_key)
        moved_rules = {}
        for (family, table, chain, handle), atoms in list(rs.rules.items()):
            if (family, table, chain) == old_key:
                moved_rules[(family, table, parts[5], handle)] = atoms
                del rs.rules[(family, table, chain, handle)]
        rs.rules.update(moved_rules)
        for (family, table, chain, handle), atoms in list(rs.rules.items()):
            if (family, table) == old_key[:2]:
                rs.rules[(family, table, chain, handle)] = tuple(
                    f"jump:{parts[5]}" if atom == f"jump:{parts[4]}" else atom
                    for atom in atoms
                )
        return None, f"rename chain {parts[2]} {parts[3]} {parts[5]} {parts[4]}"
    if noun == "rule" and len(parts) in {6, 7}:
        try:
            handle = int(parts[5])
        except ValueError:
            return "bad-handle", None
        key = (parts[2], parts[3], parts[4], handle)
        if act == "add" and len(parts) == 7:
            if key[:3] not in rs.chains:
                return "missing-chain", None
            if key in rs.rules:
                return "duplicate-rule", None
            atoms = expr(parts[6])
            reason = check_expr(rs, key[0], key[1], atoms)
            if reason:
                return reason, None
            rs.rules[key] = atoms
            return None, f"delete rule {key[0]} {key[1]} {key[2]} {key[3]}"
        if act == "delete" and len(parts) == 6:
            if key not in rs.rules:
                return "missing-rule", None
            old = rs.rules.pop(key)
            return None, f"add rule {key[0]} {key[1]} {key[2]} {key[3]} {fmt_expr(old)}"
        if act == "replace" and len(parts) == 7:
            if key not in rs.rules:
                return "missing-rule", None
            atoms = expr(parts[6])
            reason = check_expr(rs, key[0], key[1], atoms)
            if reason:
                return reason, None
            old = rs.rules[key]
            rs.rules[key] = atoms
            return None, f"replace rule {key[0]} {key[1]} {key[2]} {key[3]} {fmt_expr(old)}"
    if act == "move" and noun == "rule" and len(parts) == 7:
        try:
            old_handle = int(parts[5])
            new_handle = int(parts[6])
        except ValueError:
            return "bad-handle", None
        old_key = (parts[2], parts[3], parts[4], old_handle)
        new_key = (parts[2], parts[3], parts[4], new_handle)
        if old_key not in rs.rules:
            return "missing-rule", None
        if new_key in rs.rules:
            return "duplicate-rule", None
        rs.rules[new_key] = rs.rules.pop(old_key)
        return None, f"move rule {parts[2]} {parts[3]} {parts[4]} {new_handle} {old_handle}"
    return "syntax", None


def canonical_plan(line: str) -> str:
    parts = line.split()
    if parts[:2] == ["add", "set"]:
        return " ".join(parts[:5] + [fmt_elems(elems(parts[5]))])
    if parts[:2] == ["replace", "set"]:
        return " ".join(parts[:5] + [fmt_elems(elems(parts[5]))])
    if parts[:2] == ["add", "rule"]:
        return " ".join(parts[:6] + [fmt_expr(expr(parts[6]))])
    if parts[:2] == ["replace", "rule"]:
        return " ".join(parts[:6] + [fmt_expr(expr(parts[6]))])
    return " ".join(parts)


def expected(base: str, plan: str, state: str) -> dict[str, str]:
    base_rs = parse_base(base)
    work = base_rs.clone()
    lines = clean_lines(plan)
    inverses: list[str] = []
    reject: tuple[int, str] | None = None
    for idx, line in enumerate(lines, 1):
        reason, inverse = apply_op(work, canonical_plan(line))
        if reason:
            reject = (idx, reason)
            break
        inverses.append(inverse or "")
    state_lines = clean_lines(state)
    applying = state_lines and state_lines[0] == "applying"
    applied = [int(line.split()[1]) for line in state_lines[1:]]
    if applying and applied != list(range(1, len(applied) + 1)):
        reject = (1, "state-prefix")
        applied = []
    prefix = len(applied) if applying else 0
    report = ["op\tdecision\treason"]
    approved: list[str] = []
    if reject is None:
        for idx, line in enumerate(lines, 1):
            if applying and idx <= prefix:
                report.append(f"{idx}\tAPPROVE\talready-applied")
            else:
                report.append(f"{idx}\tAPPROVE\tapplied")
                approved.append(f"{idx:04d}\t{canonical_plan(line)}")
        final_rs = work
    else:
        bad, reason = reject
        for idx in range(1, len(lines) + 1):
            if applying and idx <= prefix:
                report.append(f"{idx}\tROLLBACK\trecovery")
            elif idx < bad:
                report.append(f"{idx}\tSKIP\tatomic-rollback")
            elif idx == bad:
                report.append(f"{idx}\tREJECT\t{reason}")
            else:
                report.append(f"{idx}\tSKIP\tblocked-by-reject")
        approved.extend(f"{idx:04d}\t{inverses[idx - 1]}" for idx in range(prefix, 0, -1))
        final_rs = base_rs
    return {
        "approved.ops": "\n".join(approved) + ("\n" if approved else ""),
        "report.tsv": "\n".join(report) + "\n",
        "final.ruleset": canonical_ruleset(final_rs),
        "state.out": "clean\n",
    }


@pytest.fixture(scope="session", autouse=True)
def build_tool():
    """Build the Rust rollout auditor once for all verifier cases."""
    subprocess.run([str(APP / "scripts" / "build.sh")], cwd=APP, check=True, text=True)
    assert BIN.exists()


def run_case(tmp_path: Path, base: str, plan: str, state: str) -> dict[str, str]:
    """Run fwplan on a dossier and return all required output files."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    case = tmp_path / "case"
    out = tmp_path / "out"
    case.mkdir()
    (case / "base.fw").write_text(base)
    (case / "plan.fw").write_text(plan)
    (case / "state.fw").write_text(state)
    originals = {
        "base.fw": base,
        "plan.fw": plan,
        "state.fw": state,
    }
    subprocess.run(
        [
            str(BIN),
            "--base",
            str(case / "base.fw"),
            "--plan",
            str(case / "plan.fw"),
            "--state",
            str(case / "state.fw"),
            "--out",
            str(out),
        ],
        check=True,
        text=True,
    )
    for name, contents in originals.items():
        assert (case / name).read_text() == contents
    assert sorted(p.name for p in out.iterdir()) == [
        "approved.ops",
        "final.ruleset",
        "report.tsv",
        "state.out",
    ]
    return {
        name: (out / name).read_text()
        for name in ["approved.ops", "report.tsv", "final.ruleset", "state.out"]
    }


BASE = """
table inet filter
set inet filter admins 10.0.0.2,10.0.0.1,10.0.0.2
chain inet filter input input 0
chain inet filter audit - 0
rule inet filter input 10 set:admins;jump:audit;verdict:accept
"""


def assert_case(tmp_path: Path, base: str, plan: str, state: str = "clean\n"):
    got = run_case(tmp_path, base, plan, state)
    want = expected(base, plan, state)
    assert got == want


def test_accepts_canonical_safe_rollout(tmp_path):
    """Safe plans are approved with canonical set elements, sorted final ruleset rows, and existing references honored."""
    plan = """
    add set inet filter blocked 203.0.113.8,198.51.100.7,203.0.113.8
    add chain inet filter quarantine - 0
    add rule inet filter input 20 set:blocked;jump:quarantine;verdict:drop
    add rule inet filter quarantine 1 verdict:drop
    """
    assert_case(tmp_path, BASE, plan)


def test_rejects_out_of_order_references_atomically(tmp_path):
    """A rule cannot rely on a chain that appears only later in the same change plan."""
    plan = """
    add rule inet filter input 20 jump:late;verdict:accept
    add chain inet filter late - 0
    """
    assert_case(tmp_path, BASE, plan)


@pytest.mark.parametrize(
    "label, plan",
    [
        ("syntax", "rename table inet filter edge\n"),
        ("bad-verdict", "add rule inet filter input 20 verdict:queue\n"),
        ("bad-handle", "add rule inet filter input handle-x verdict:accept\n"),
        ("bad-priority", "add chain inet filter bad input priority-x\n"),
    ],
)
def test_rejects_malformed_plan_records_without_partial_outputs(tmp_path, label, plan):
    """Malformed records and invalid scalar fields reject atomically while still producing the required output set."""
    assert_case(tmp_path / label, BASE, plan)


@pytest.mark.parametrize(
    "label, plan",
    [
        ("table", "add table inet filter\n"),
        ("set", "add set inet filter admins 10.9.0.1\n"),
        ("chain", "add chain inet filter input - 10\n"),
        ("rule", "add rule inet filter input 10 verdict:drop\n"),
    ],
)
def test_rejects_duplicate_existing_ruleset_objects(tmp_path, label, plan):
    """Adding an object already present in the base ruleset rejects the entire rollout."""
    assert_case(tmp_path / label, BASE, plan)


@pytest.mark.parametrize(
    "label, base, plan",
    [
        (
            "add-delete-empty",
            BASE,
            """
            add table ip transient
            delete table ip transient
            """,
        ),
        (
            "delete-nonempty-base",
            BASE,
            "delete table inet filter\n",
        ),
        (
            "delete-nonempty-after-add",
            "table ip staging\n",
            """
            add chain ip staging prerouting prerouting -100
            delete table ip staging
            """,
        ),
        (
            "delete-missing",
            BASE,
            "delete table ip missing\n",
        ),
    ],
)
def test_enforces_table_add_delete_safety(tmp_path, label, base, plan):
    """Table operations are approved only when the target existence and emptiness constraints are satisfied."""
    assert_case(tmp_path / label, base, plan)


def test_rejects_dependency_deletes_and_hook_collisions(tmp_path):
    """Deletes and base-chain additions must preserve rule references, non-empty objects, and hook-priority uniqueness."""
    delete_plan = """
    delete set inet filter admins
    delete chain inet filter input
    """
    assert_case(tmp_path / "delete", BASE, delete_plan)
    hook_plan = """
    add chain inet filter ingress input 0
    add rule inet filter ingress 1 verdict:accept
    """
    assert_case(tmp_path / "hook", BASE, hook_plan)


def test_recovers_interrupted_accepted_suffix(tmp_path):
    """An interrupted valid rollout treats a contiguous applied prefix as complete and emits only the remaining safe suffix."""
    plan = """
    add chain inet filter staged - 0
    add set inet filter office 10.1.0.2,10.1.0.1
    add rule inet filter staged 5 set:office;verdict:accept
    """
    state = """
    applying
    applied 1
    applied 2
    """
    assert_case(tmp_path, BASE, plan, state)


def test_recovers_interrupted_rejected_prefix_with_inverse_operations(tmp_path):
    """An interrupted invalid rollout emits rollback operations for the applied prefix and restores the base ruleset."""
    plan = """
    add chain inet filter temporary - 0
    add rule inet filter temporary 1 jump:missing;verdict:accept
    add set inet filter never 192.0.2.9
    """
    state = """
    applying
    applied 1
    """
    assert_case(tmp_path, BASE, plan, state)


def test_renames_referenced_chain_and_replaces_sets_canonically(tmp_path):
    """Chain renames rewrite same-table jumps and moved rules while set replacements preserve references."""
    plan = """
    replace set inet filter admins 10.0.0.9,10.0.0.3,10.0.0.9
    rename chain inet filter audit review
    add rule inet filter review 2 verdict:return
    add rule inet filter input 30 set:admins;jump:review;verdict:accept
    """
    assert_case(tmp_path, BASE, plan)


def test_rejects_unsafe_chain_rename_and_rolls_back_set_replacement(tmp_path):
    """An interrupted rollout rolls back a replaced set before rejecting a rename to an occupied chain name."""
    plan = """
    replace set inet filter admins 10.0.0.7,10.0.0.6
    rename chain inet filter audit input
    add rule inet filter audit 4 verdict:drop
    """
    state = """
    applying
    applied 1
    """
    assert_case(tmp_path, BASE, plan, state)


def test_replaces_and_moves_rules_with_canonical_recovery(tmp_path):
    """Rule replacement and handle moves must preserve references, ordering, and inverse recovery semantics."""
    plan = """
    add set inet filter guests 198.51.100.11,198.51.100.10
    replace rule inet filter input 10 set:guests;jump:audit;verdict:drop
    move rule inet filter input 10 25
    add rule inet filter input 10 set:admins;verdict:accept
    """
    assert_case(tmp_path / "clean", BASE, plan)
    state = """
    applying
    applied 1
    applied 2
    applied 3
    """
    assert_case(tmp_path / "recovery", BASE, plan, state)


def test_rejects_rule_rewrite_and_bad_state_prefix_without_leaking_partial_state(tmp_path):
    """Unsafe rule rewrites and non-contiguous recovery state reject atomically with stable reports."""
    replace_plan = """
    add set inet filter scratch 192.0.2.10
    replace rule inet filter input 10 set:missing;jump:audit;verdict:accept
    move rule inet filter input 10 40
    """
    assert_case(tmp_path / "bad-replace", BASE, replace_plan)
    move_plan = """
    move rule inet filter input 10 10
    replace rule inet filter input 10 verdict:drop
    """
    assert_case(tmp_path / "bad-move", BASE, move_plan)
    state = """
    applying
    applied 1
    applied 3
    """
    assert_case(tmp_path / "bad-state", BASE, "replace rule inet filter input 10 verdict:drop\n", state)


def test_handles_larger_generated_plan_without_fixture_constants(tmp_path):
    """A larger same-schema dossier checks deterministic ordering across families, handles, and element permutations."""
    base = """
    table inet filter
    table ip nat
    chain inet filter input input 0
    chain ip nat prerouting prerouting -100
    set inet filter trusted zeta,alpha,beta,alpha
    rule inet filter input 1 set:trusted;verdict:accept
    """
    plan_lines = []
    for idx, name in enumerate(["dmz", "corp", "guest", "ops"], 1):
        plan_lines.append(
            f"add set inet filter {name} 10.0.{idx}.3,10.0.{idx}.1,10.0.{idx}.2,10.0.{idx}.1"
        )
        plan_lines.append(f"add chain inet filter {name}_chain - 0")
        plan_lines.append(
            f"add rule inet filter input {idx + 10} set:{name};jump:{name}_chain;verdict:accept"
        )
        plan_lines.append(f"add rule inet filter {name}_chain {idx} verdict:drop")
    plan_lines.append("add rule ip nat prerouting 50 verdict:accept")
    plan_lines.append("replace set inet filter trusted gamma,beta,alpha,beta")
    plan_lines.append("rename chain inet filter ops_chain operations")
    plan_lines.append("add rule inet filter operations 99 verdict:return")
    assert_case(tmp_path, base, "\n".join(plan_lines) + "\n")
