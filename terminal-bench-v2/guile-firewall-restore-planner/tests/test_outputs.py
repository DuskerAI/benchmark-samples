import json
import os
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/fwrestore")


def build_planner() -> None:
    subprocess.run([str(APP / "scripts/build.sh")], check=True, cwd=APP)


def run_case(
    tmp_path: Path, files: dict[str, str]
) -> tuple[dict, list[dict[str, str]]]:
    build_planner()
    case_dir = tmp_path / "case"
    out_dir = tmp_path / "out"
    case_dir.mkdir()
    for relative, content in files.items():
        target = case_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(
        [str(APP / "bin/fwplan"), "--case", str(case_dir), "--out", str(out_dir)],
        check=True,
        cwd=APP,
    )
    assert sorted(path.name for path in out_dir.iterdir()) == [
        "plan.json",
        "service-audit.tsv",
    ]
    raw_plan = (out_dir / "plan.json").read_text(encoding="utf-8")
    top_pairs = json.loads(raw_plan, object_pairs_hook=list)
    assert [key for key, _value in top_pairs] == [
        "status",
        "reason",
        "rollback_to",
        "transactions",
        "chains",
        "rules",
    ]
    plan = json.loads(raw_plan)
    audit_lines = (
        (out_dir / "service-audit.tsv").read_text(encoding="utf-8").splitlines()
    )
    assert audit_lines[0] == "service\towner\texposure\tallowed_zones\tstatus"
    rows = [
        dict(zip(audit_lines[0].split("\t"), line.split("\t"), strict=True))
        for line in audit_lines[1:]
    ]
    return plan, rows


def test_planner_stays_offline_and_does_not_touch_firewall_tools(tmp_path: Path):
    """The planner reviews restore input without network syscalls or firewall tool calls."""
    build_planner()
    case_dir = tmp_path / "case"
    out_dir = tmp_path / "out"
    shim_dir = tmp_path / "shims"
    trace_file = tmp_path / "network.trace"
    calls_file = tmp_path / "firewall.calls"
    case_dir.mkdir()
    shim_dir.mkdir()
    (case_dir / "root.fw").write_text(
        """
chain inet filter input priority 0 policy drop
service api owner ops exposure public ports tcp:443 zones corp
allow inet filter input tcp 443 service api zones corp ticket T1
checkpoint live committed
""",
        encoding="utf-8",
    )
    firewall_tools = [
        "nft",
        "iptables",
        "ip6tables",
        "iptables-restore",
        "ip6tables-restore",
    ]
    for tool in firewall_tools:
        shim = shim_dir / tool
        shim.write_text(
            "#!/bin/sh\n"
            f"printf '%s %s\\n' '{tool}' \"$*\" >> '{calls_file}'\n"
            "exit 97\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{shim_dir}:{env['PATH']}"

    subprocess.run(
        [
            "strace",
            "-f",
            "-e",
            "trace=network",
            "-o",
            str(trace_file),
            str(APP / "bin/fwplan"),
            "--case",
            str(case_dir),
            "--out",
            str(out_dir),
        ],
        check=True,
        cwd=APP,
        env=env,
    )

    if calls_file.exists():
        pytest.fail(calls_file.read_text(encoding="utf-8"))
    trace = trace_file.read_text(encoding="utf-8")
    assert "AF_INET" not in trace and "AF_INET6" not in trace


def test_command_line_accepts_only_documented_argument_order(tmp_path: Path):
    """The executable accepts exactly --case CASEDIR --out OUTDIR and rejects variants."""
    build_planner()
    case_dir = tmp_path / "case"
    out_dir = tmp_path / "out"
    case_dir.mkdir()
    (case_dir / "root.fw").write_text("checkpoint cp1 committed\n", encoding="utf-8")

    invalid_invocations = [
        ["--out", str(out_dir), "--case", str(case_dir)],
        ["--case", str(case_dir), "--out", str(out_dir), "--extra"],
        ["--case", str(case_dir)],
    ]
    for args in invalid_invocations:
        result = subprocess.run(
            [str(APP / "bin/fwplan"), *args],
            check=False,
            cwd=APP,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "usage: fwplan --case CASEDIR --out OUTDIR" in result.stdout


def test_tabs_comments_and_include_expansion_are_normal_fragment_syntax(tmp_path: Path):
    """Whitespace-separated records and trailing comments parse across included files."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
include	fragments/base.fw   # include text after tabs is ignored
service	portal	owner	edge	exposure	public	ports	tcp:443	zones	corp,dmz # tabs
allow inet filter input tcp 443 service portal zones dmz,corp ticket P2

checkpoint live committed # trailing comment
""",
            "fragments/base.fw": """
chain	inet	filter	input	priority	0	policy	drop
allow	inet	filter	input	tcp	80	service	portal	zones	corp	ticket	OLD
""",
        },
    )
    assert plan["status"] == "apply"
    assert plan["reason"] == ""
    assert plan["rollback_to"] == "live"
    assert [rule["port"] for rule in plan["rules"]] == [80, 443]
    assert audit == [
        {
            "service": "portal",
            "owner": "edge",
            "exposure": "public",
            "allowed_zones": "corp,dmz",
            "status": "expanded",
        }
    ]


def test_include_precedence_and_service_audit_are_deterministic(tmp_path: Path):
    """Included fragments expand in place and effective services/rules drive the audit."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
include fragments/base.fw
chain inet filter input priority 5 policy accept
service api owner ops exposure public ports tcp:443 zones corp
allow inet filter input tcp 443 service api zones corp ticket T2
checkpoint cp1 committed
""",
            "fragments/base.fw": """
service api owner old exposure public ports tcp:443 zones dmz
chain inet filter input priority 0 policy drop
allow inet filter input tcp 443 service api zones dmz ticket T1
""",
        },
    )
    assert plan["status"] == "apply"
    assert plan["rollback_to"] == "cp1"
    assert plan["chains"] == [
        {
            "family": "inet",
            "table": "filter",
            "name": "input",
            "priority": 5,
            "policy": "accept",
        }
    ]
    assert [rule["zones"] for rule in plan["rules"]] == [["corp"], ["dmz"]]
    assert audit == [
        {
            "service": "api",
            "owner": "ops",
            "exposure": "public",
            "allowed_zones": "corp,dmz",
            "status": "expanded",
        }
    ]


def test_nested_include_paths_are_relative_to_the_including_fragment(tmp_path: Path):
    """Nested includes resolve from the fragment that names the next file."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
include fragments/base.fw
checkpoint live committed
""",
            "fragments/base.fw": """
service admin owner ops exposure internal ports tcp:8443 zones mgmt
include deeper/rules.fw
""",
            "fragments/deeper/rules.fw": """
chain inet filter input priority 0 policy drop
allow inet filter input tcp 8443 service admin zones mgmt ticket NESTED
""",
        },
    )
    assert plan["status"] == "apply"
    assert plan["rollback_to"] == "live"
    assert [rule["ticket"] for rule in plan["rules"]] == ["NESTED"]
    assert audit == [
        {
            "service": "admin",
            "owner": "ops",
            "exposure": "internal",
            "allowed_zones": "mgmt",
            "status": "ok",
        }
    ]


def test_rollback_uses_previous_committed_state(tmp_path: Path):
    """Prepared or failed restore state rolls audit material back to the prior commit."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
chain inet filter input priority 0 policy drop
service ssh owner core exposure internal ports tcp:22 zones admin
allow inet filter input tcp 22 service ssh zones admin ticket C1
checkpoint stable committed
allow inet filter input tcp 22 service ssh zones wan ticket C2
checkpoint trial failed
""",
        },
    )
    assert plan["status"] == "rollback"
    assert plan["rollback_to"] == "stable"
    assert [tx["status"] for tx in plan["transactions"]] == ["committed", "failed"]
    assert [rule["ticket"] for rule in plan["rules"]] == ["C1"]
    assert audit[0]["allowed_zones"] == "admin"
    assert audit[0]["status"] == "ok"


def test_prepared_checkpoint_rolls_back_to_prior_committed_state(tmp_path: Path):
    """A prepared final checkpoint uses the previous committed prefix for outputs."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
chain inet filter input priority 0 policy drop
service ssh owner core exposure internal ports tcp:22 zones admin
allow inet filter input tcp 22 service ssh zones admin ticket C1
checkpoint stable committed
allow inet filter input tcp 22 service ssh zones wan ticket C2
service ssh owner core exposure internal ports tcp:22 zones admin,wan
checkpoint trial prepared
""",
        },
    )
    assert plan["status"] == "rollback"
    assert plan["reason"] == ""
    assert plan["rollback_to"] == "stable"
    assert [tx["status"] for tx in plan["transactions"]] == ["committed", "prepared"]
    assert [rule["ticket"] for rule in plan["rules"]] == ["C1"]
    assert plan["rules"][0]["zones"] == ["admin"]
    assert audit == [
        {
            "service": "ssh",
            "owner": "core",
            "exposure": "internal",
            "allowed_zones": "admin",
            "status": "ok",
        }
    ]


def test_chain_priority_conflicts_reject_atomically(tmp_path: Path):
    """Different chains in one family/table cannot share a restore priority."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
chain inet filter input priority 0 policy drop
chain inet filter forward priority 0 policy drop
service web owner edge exposure public ports tcp:443 zones dmz
allow inet filter input tcp 443 service web zones dmz ticket W1
checkpoint cp1 committed
""",
        },
    )
    assert plan["status"] == "reject"
    assert plan["reason"] == "chain-priority-conflict"
    assert audit == []


def test_rule_identity_uses_sorted_zone_sets_and_deny_override(tmp_path: Path):
    """Rule identity treats zone order as canonical before deciding the final action."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
service dns owner net exposure public ports udp:53 zones branch,corp
chain inet filter input priority -5 policy drop
allow inet filter input udp 53 service dns zones corp,branch ticket D1
deny inet filter input udp 53 service dns zones branch,corp ticket D2
checkpoint live committed
""",
        },
    )
    assert plan["status"] == "apply"
    assert len(plan["rules"]) == 1
    assert plan["rules"][0]["action"] == "deny"
    assert plan["rules"][0]["ticket"] == "D2"
    assert plan["rules"][0]["zones"] == ["branch", "corp"]
    assert audit[0]["allowed_zones"] == ""
    assert audit[0]["status"] == "closed"


def test_chain_sorting_uses_family_table_priority_and_name(tmp_path: Path):
    """Restore chains are emitted in the documented multi-key order."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
chain ip nat prerouting priority -100 policy accept
chain inet filter output priority 5 policy drop
chain inet filter input priority 0 policy drop
chain ip filter ingress priority 0 policy drop
chain inet filter forward priority -5 policy accept
chain inet mangle output priority -50 policy accept
checkpoint live committed
""",
        },
    )
    assert plan["status"] == "apply"
    assert [
        (chain["family"], chain["table"], chain["priority"], chain["name"])
        for chain in plan["chains"]
    ] == [
        ("inet", "filter", -5, "forward"),
        ("inet", "filter", 0, "input"),
        ("inet", "filter", 5, "output"),
        ("inet", "mangle", -50, "output"),
        ("ip", "filter", 0, "ingress"),
        ("ip", "nat", -100, "prerouting"),
    ]
    assert audit == []


def test_rule_sorting_uses_all_documented_keys(tmp_path: Path):
    """Rules are emitted by family, table, chain, protocol, port, service, and zones."""
    plan, _audit = run_case(
        tmp_path,
        {
            "root.fw": """
service alpha owner ops exposure internal ports tcp:22,tcp:80,tcp:443,tcp:8080,udp:53 zones corp,dmz
service beta owner ops exposure internal ports tcp:80 zones corp
chain ip filter c1 priority 0 policy drop
chain inet nat c0 priority 0 policy drop
chain inet filter c2 priority 20 policy drop
chain inet filter c1 priority 10 policy drop
allow ip filter c1 tcp 80 service alpha zones corp ticket G
allow inet filter c1 tcp 443 service alpha zones corp ticket C
allow inet filter c1 tcp 80 service beta zones corp ticket B
allow inet filter c1 udp 53 service alpha zones dmz,corp ticket D
allow inet nat c0 tcp 8080 service alpha zones corp ticket F
allow inet filter c2 tcp 22 service alpha zones corp ticket E
allow inet filter c1 tcp 80 service alpha zones dmz ticket A
checkpoint live committed
""",
        },
    )
    assert plan["status"] == "apply"
    assert [
        (
            rule["family"],
            rule["table"],
            rule["chain"],
            rule["proto"],
            rule["port"],
            rule["service"],
            ",".join(rule["zones"]),
            rule["ticket"],
        )
        for rule in plan["rules"]
    ] == [
        ("inet", "filter", "c1", "tcp", 80, "alpha", "dmz", "A"),
        ("inet", "filter", "c1", "tcp", 80, "beta", "corp", "B"),
        ("inet", "filter", "c1", "tcp", 443, "alpha", "corp", "C"),
        ("inet", "filter", "c1", "udp", 53, "alpha", "corp,dmz", "D"),
        ("inet", "filter", "c2", "tcp", 22, "alpha", "corp", "E"),
        ("inet", "nat", "c0", "tcp", 8080, "alpha", "corp", "F"),
        ("ip", "filter", "c1", "tcp", 80, "alpha", "corp", "G"),
    ]


def test_public_service_with_extra_allowed_port_is_expanded(tmp_path: Path):
    """Public services report expanded when active allows add an undeclared port."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
chain inet filter input priority 0 policy drop
service portal owner sécurité exposure public ports tcp:443 zones corp
allow inet filter input tcp 443 service portal zones corp ticket P1
allow inet filter input tcp 8443 service portal zones corp ticket P2
checkpoint live committed
""",
        },
    )
    assert plan["status"] == "apply"
    assert [rule["port"] for rule in plan["rules"]] == [443, 8443]
    assert audit == [
        {
            "service": "portal",
            "owner": "sécurité",
            "exposure": "public",
            "allowed_zones": "corp",
            "status": "expanded",
        }
    ]


def test_include_safety_rejections_still_emit_artifacts(tmp_path: Path):
    """Unsafe include graphs are rejected while preserving both output artifacts."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": "include a.fw\ncheckpoint cp1 committed\n",
            "a.fw": "include nested/../a.fw\n",
        },
    )
    assert plan == {
        "status": "reject",
        "reason": "include-cycle",
        "rollback_to": None,
        "transactions": [],
        "chains": [],
        "rules": [],
    }
    assert audit == []


def test_unreadable_include_rejects_with_empty_audit(tmp_path: Path):
    """An include target that cannot be read as a fragment is rejected atomically."""
    build_planner()
    case_dir = tmp_path / "case"
    out_dir = tmp_path / "out"
    case_dir.mkdir()
    (case_dir / "root.fw").write_text(
        "include fragments/secret.fw\ncheckpoint cp1 committed\n",
        encoding="utf-8",
    )
    (case_dir / "fragments").mkdir()
    (case_dir / "fragments/secret.fw").mkdir()

    subprocess.run(
        [str(APP / "bin/fwplan"), "--case", str(case_dir), "--out", str(out_dir)],
        check=True,
        cwd=APP,
    )
    plan = json.loads((out_dir / "plan.json").read_text(encoding="utf-8"))
    audit_lines = (
        (out_dir / "service-audit.tsv").read_text(encoding="utf-8").splitlines()
    )
    assert plan == {
        "status": "reject",
        "reason": "missing-include",
        "rollback_to": None,
        "transactions": [],
        "chains": [],
        "rules": [],
    }
    assert audit_lines == ["service\towner\texposure\tallowed_zones\tstatus"]


def test_service_audit_status_matrix_and_row_sorting(tmp_path: Path):
    """Service rows are sorted and classify public, closed, and exposed services."""
    plan, audit = run_case(
        tmp_path,
        {
            "root.fw": """
chain inet filter input priority -10 policy drop
service zeta owner app exposure internal ports tcp:8080 zones mgmt
service alpha owner edge exposure public ports tcp:443 zones dmz,corp
service beta owner edge exposure public ports tcp:25 zones dmz
allow inet filter input tcp 443 service alpha zones corp,dmz ticket A1
allow inet filter input tcp 8080 service zeta zones wan ticket Z1
checkpoint cp1 committed
""",
        },
    )
    assert plan["status"] == "apply"
    assert [row["service"] for row in audit] == ["alpha", "beta", "zeta"]
    assert [row["status"] for row in audit] == ["ok", "closed", "exposed"]
    assert audit[0]["allowed_zones"] == "corp,dmz"
    assert audit[2]["allowed_zones"] == "wan"


@pytest.mark.parametrize(
    ("reason", "files"),
    [
        (
            "missing-include",
            {"root.fw": "include absent.fw\ncheckpoint cp1 committed\n"},
        ),
        (
            "include-outside-case",
            {"root.fw": "include ../outside.fw\ncheckpoint cp1 committed\n"},
        ),
        (
            "bad-record",
            {"root.fw": "nft add table inet filter\ncheckpoint cp1 committed\n"},
        ),
        (
            "bad-record",
            {"root.fw": "include fragments/base.fw extra\ncheckpoint cp1 committed\n"},
        ),
        (
            "bad-record",
            {
                "root.fw": (
                    "service web owner edge exposure public ports tcp:443\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-record",
            {
                "root.fw": (
                    "service web owner edge exposure partner ports tcp:443 zones dmz\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-record",
            {
                "root.fw": (
                    "chain inet filter input priority 0 policy drop extra\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-record",
            {
                "root.fw": (
                    "chain inet filter input priority 0 policy reject\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-record",
            {
                "root.fw": (
                    "chain inet filter input priority high policy drop\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-record",
            {
                "root.fw": (
                    "chain inet filter input priority 0 policy drop\n"
                    "service web owner edge exposure public ports tcp:443 zones dmz\n"
                    "allow inet filter input tcp 443 service web zones dmz ticket\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-record",
            {"root.fw": "checkpoint cp1 committed extra\n"},
        ),
        (
            "bad-port",
            {
                "root.fw": (
                    "service web owner edge exposure public ports tcp:70000 zones dmz\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "bad-checkpoint",
            {"root.fw": "checkpoint cp1 abandoned\n"},
        ),
        (
            "unknown-chain",
            {
                "root.fw": (
                    "service web owner edge exposure public ports tcp:443 zones dmz\n"
                    "allow inet filter input tcp 443 service web zones dmz ticket W1\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "unknown-service",
            {
                "root.fw": (
                    "chain inet filter input priority 0 policy drop\n"
                    "allow inet filter input tcp 443 service web zones dmz ticket W1\n"
                    "checkpoint cp1 committed\n"
                )
            },
        ),
        (
            "no-usable-checkpoint",
            {
                "root.fw": (
                    "chain inet filter input priority 0 policy drop\n"
                    "service web owner edge exposure public ports tcp:443 zones dmz\n"
                )
            },
        ),
        (
            "no-committed-checkpoint",
            {
                "root.fw": (
                    "chain inet filter input priority 0 policy drop\n"
                    "service web owner edge exposure public ports tcp:443 zones dmz\n"
                    "checkpoint trial failed\n"
                )
            },
        ),
    ],
)
def test_documented_rejection_reasons_emit_empty_audits(
    tmp_path: Path, reason: str, files: dict[str, str]
):
    """Documented rejection categories return their exact public reason strings."""
    plan, audit = run_case(tmp_path, files)
    assert plan["status"] == "reject"
    assert plan["reason"] == reason
    assert plan["rollback_to"] is None
    assert plan["transactions"] == []
    assert plan["chains"] == []
    assert plan["rules"] == []
    assert audit == []
