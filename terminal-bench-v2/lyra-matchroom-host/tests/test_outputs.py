import grp
import hashlib
import pwd
import re
import stat
import subprocess
from pathlib import Path

EXPECTED_GROUPS = {
    "lyra-score",
    "lyra-chief",
    "lyra-audit",
    "lyra-emergency",
}

EXPECTED_USERS = {
    "score01": {"primary": "score01", "groups": {"lyra-score"}},
    "score02": {"primary": "score02", "groups": {"lyra-score"}},
    "chief01": {"primary": "chief01", "groups": {"lyra-chief", "lyra-score", "lyra-emergency"}},
    "auditor01": {"primary": "auditor01", "groups": {"lyra-audit"}},
    "guest01": {"primary": "guest01", "groups": set()},
}

EXPECTED_DIRS = {
    "/srv/lyra/incoming": ("root", "lyra-score", 0o2770),
    "/srv/lyra/sealed": ("root", "lyra-chief", 0o2750),
    "/srv/lyra/rulings": ("root", "lyra-chief", 0o2770),
    "/srv/lyra/audit": ("root", "lyra-audit", 0o0750),
}


def read(path):
    return Path(path).read_text(encoding="utf-8")


def meaningful_lines(path):
    lines = []
    for raw in read(path).splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(" ".join(line.split()))
    return lines


def mode(path):
    return stat.S_IMODE(Path(path).stat().st_mode)


def run_as(user, command, *, check=True):
    return subprocess.run(["runuser", "-u", user, "--", "bash", "-lc", command], check=check, text=True)


def chief_seal(name, *, check=True):
    return run_as("chief01", f"sudo -n /usr/local/sbin/lyra-seal {name}", check=check)


def install_ruling(name, owner, payload, *, expires="2099-12-31T23:59:59Z", nonce="0123456789abcdef"):
    ruling = Path("/srv/lyra/rulings") / f"{name}.ruling"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    ruling.write_text(
        f"artifact={name}\nowner={owner}\nsha256={digest}\nexpires={expires}\nnonce={nonce}\n",
        encoding="utf-8",
    )
    subprocess.run(["chown", "root:lyra-chief", str(ruling)], check=True)
    subprocess.run(["chmod", "0640", str(ruling)], check=True)
    return digest


def stage_artifact(owner, name, payload, *, mode="0640", expires="2099-12-31T23:59:59Z", nonce="0123456789abcdef"):
    src = Path("/srv/lyra/incoming") / name
    src.write_text(payload, encoding="utf-8")
    subprocess.run(["chown", f"{owner}:lyra-score", str(src)], check=True)
    subprocess.run(["chmod", mode, str(src)], check=True)
    return install_ruling(name, owner, payload, expires=expires, nonce=nonce)


def group_memberships(user):
    return {g.gr_name for g in grp.getgrall() if user in g.gr_mem}


def require_setting(lines, key, expected, *, absent_ok=False):
    values = [line for line in lines if line.split(maxsplit=1)[0] == key]
    if absent_ok and not values:
        return
    assert expected in values
    assert all(line == expected for line in values)


def assert_audit_event(audit_text, action, artifact, detail):
    pattern = (
        rf"^\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}Z "
        rf"action={action} artifact={re.escape(artifact)} {re.escape(detail)}$"
    )
    assert re.search(pattern, audit_text, re.MULTILINE)


def test_users_groups_and_locked_passwords():
    """The match-room accounts and groups match the access policy."""
    for group in EXPECTED_GROUPS:
        grp.getgrnam(group)

    shadow = read("/etc/shadow")
    shadow_by_user = {line.split(":", 2)[0]: line.split(":", 2)[1] for line in shadow.splitlines()}
    for user, expected in EXPECTED_USERS.items():
        entry = pwd.getpwnam(user)
        assert entry.pw_dir == f"/home/{user}"
        assert entry.pw_shell == "/bin/bash"
        assert grp.getgrgid(entry.pw_gid).gr_name == expected["primary"]
        assert group_memberships(user) == expected["groups"]
        assert shadow_by_user[user].startswith(("!", "*"))


def test_service_directories_and_acl_defaults():
    """The /srv/lyra hierarchy has exact ownership, modes, and default ACLs."""
    for path, (owner, group, expected_mode) in EXPECTED_DIRS.items():
        st = Path(path).stat()
        assert pwd.getpwuid(st.st_uid).pw_name == owner
        assert grp.getgrgid(st.st_gid).gr_name == group
        assert mode(path) == expected_mode

    acl = subprocess.check_output(["getfacl", "-cp", "/srv/lyra/incoming"], text=True)
    assert "default:group:lyra-chief:rwx" in acl.splitlines()
    assert "default:other::---" in acl.splitlines()


def test_sudoers_policy_is_exact_and_valid():
    """The sudo policy grants chief/emergency access and denies score-only users."""
    sudoers = Path("/etc/sudoers.d/lyra-matchroom")
    assert sudoers.exists()
    assert mode(sudoers) == 0o440
    subprocess.run(["visudo", "-cf", str(sudoers)], check=True)
    lines = meaningful_lines(sudoers)
    chief_re = re.compile(r"^%lyra-chief\s+ALL\s*=\s*\(root\)\s+NOPASSWD:\s*/usr/local/sbin/lyra-seal$")
    emergency_re = re.compile(r"^%lyra-emergency\s+ALL\s*=\s*\(root\)\s+NOPASSWD:\s*/usr/bin/systemctl restart ssh$")
    deny_re = re.compile(r"^(score0[12])\s+ALL\s*=\s*\((ALL|ALL:ALL)\)\s+!ALL$")
    chief_indexes = [i for i, line in enumerate(lines) if chief_re.match(line)]
    emergency_indexes = [i for i, line in enumerate(lines) if emergency_re.match(line)]
    deny_users = {m.group(1): i for i, line in enumerate(lines) if (m := deny_re.match(line))}
    sudoers_policy = "\n".join(lines)

    assert len(chief_indexes) == 1
    assert len(emergency_indexes) == 1
    assert deny_users == {"score01": deny_users.get("score01"), "score02": deny_users.get("score02")}
    grant_index = max(chief_indexes[0], emergency_indexes[0])
    assert deny_users["score01"] > grant_index
    assert deny_users["score02"] > grant_index
    assert "%lyra-score" not in sudoers_policy
    assert "auditor01" not in sudoers_policy
    assert "guest01" not in sudoers_policy


def test_lyra_seal_executable_and_successful_runtime_flow():
    """Chiefs can seal a real score artifact; score users lose access afterward."""
    seal = Path("/usr/local/sbin/lyra-seal")
    assert seal.exists()
    assert pwd.getpwuid(seal.stat().st_uid).pw_name == "root"
    assert grp.getgrgid(seal.stat().st_gid).gr_name == "lyra-chief"
    assert mode(seal) & 0o750 == 0o750

    name = "round-alpha.result"
    src = Path("/srv/lyra/incoming") / name
    dst = Path("/srv/lyra/sealed") / name
    audit = Path("/srv/lyra/audit/seal.log")
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)

    payload = "board=7 score=41\n"
    before_hash = stage_artifact("score01", name, payload)
    chief_seal(name)

    assert not src.exists()
    assert dst.read_text() == payload
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before_hash
    st = dst.stat()
    assert pwd.getpwuid(st.st_uid).pw_name == "root"
    assert grp.getgrgid(st.st_gid).gr_name == "lyra-chief"
    assert mode(dst) == 0o640
    run_as("chief01", f"grep -qx 'board=7 score=41' {dst}")
    assert run_as("score02", f"cat {dst}", check=False).returncode != 0

    audit_st = audit.stat()
    assert pwd.getpwuid(audit_st.st_uid).pw_name == "root"
    assert grp.getgrgid(audit_st.st_gid).gr_name == "lyra-audit"
    assert mode(audit) == 0o640
    audit_text = audit.read_text(encoding="utf-8")
    assert_audit_event(audit_text, "sealed", name, f"sha256={before_hash}")


def test_lyra_seal_accepts_each_authorized_source_owner():
    """Artifacts owned by score02 and chief01 follow the same provenance policy."""
    for user in ["score02", "chief01"]:
        name = f"owner-{user}.result"
        src = Path("/srv/lyra/incoming") / name
        dst = Path("/srv/lyra/sealed") / name
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)

        stage_artifact(user, name, f"{user}-payload\n", nonce=f"00000000000000{len(user):02x}")
        chief_seal(name)

        assert not src.exists()
        assert dst.read_text() == f"{user}-payload\n"
        assert pwd.getpwuid(dst.stat().st_uid).pw_name == "root"
        assert grp.getgrgid(dst.stat().st_gid).gr_name == "lyra-chief"
        assert mode(dst) == 0o640


def test_lyra_seal_is_idempotent_without_rewriting_sealed_artifact():
    """A repeated seal succeeds only as an already-sealed no-op with audit evidence."""
    name = "round-beta.result"
    src = Path("/srv/lyra/incoming") / name
    dst = Path("/srv/lyra/sealed") / name
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)

    stage_artifact("score01", name, "locked-state\n")
    chief_seal(name)
    first_stat = dst.stat()
    first_bytes = dst.read_bytes()
    chief_seal(name)

    assert not src.exists()
    assert dst.read_bytes() == first_bytes
    assert dst.stat().st_ino == first_stat.st_ino
    audit_text = Path("/srv/lyra/audit/seal.log").read_text(encoding="utf-8")
    digest = hashlib.sha256(first_bytes).hexdigest()
    assert_audit_event(audit_text, "sealed", name, f"sha256={digest}")
    assert_audit_event(audit_text, "already-sealed", name, f"sha256={digest}")


def test_lyra_seal_rejects_bad_argument_counts_and_empty_names():
    """Argument validation failures use the documented audit artifact and reason."""
    audit = Path("/srv/lyra/audit/seal.log")
    before_audit = audit.read_text(encoding="utf-8") if audit.exists() else ""

    assert run_as("chief01", "sudo -n /usr/local/sbin/lyra-seal", check=False).returncode != 0
    assert run_as("chief01", "sudo -n /usr/local/sbin/lyra-seal ''", check=False).returncode != 0
    assert run_as("chief01", "sudo -n /usr/local/sbin/lyra-seal one.result two.result", check=False).returncode != 0

    after_audit = audit.read_text(encoding="utf-8")
    assert after_audit.startswith(before_audit)
    assert len(re.findall(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z action=rejected artifact=INVALID_NAME reason=invalid-basename$", after_audit, re.MULTILINE)) >= 3


def test_lyra_seal_rejects_unsafe_inputs_without_state_corruption():
    """Traversal, symlink, world-writable, and overwrite attempts are rejected."""
    audit = Path("/srv/lyra/audit/seal.log")
    before_audit = audit.read_text(encoding="utf-8") if audit.exists() else ""
    protected = Path("/srv/lyra/sealed/protected.result")
    collision = Path("/srv/lyra/incoming/protected.result")
    symlink = Path("/srv/lyra/incoming/link.result")
    loose = Path("/srv/lyra/incoming/loose.result")
    directory = Path("/srv/lyra/incoming/dir.result")

    for path in [protected, collision, symlink, loose]:
        path.unlink(missing_ok=True)
    if directory.exists():
        subprocess.run(["rm", "-rf", str(directory)], check=True)

    protected.write_text("official\n", encoding="utf-8")
    subprocess.run(["chown", "root:lyra-chief", str(protected)], check=True)
    subprocess.run(["chmod", "0640", str(protected)], check=True)
    stage_artifact("score01", "protected.result", "attacker\n")
    run_as("score01", f"ln -s /etc/passwd {symlink}")
    install_ruling("link.result", "score01", "irrelevant\n")
    stage_artifact("score01", "loose.result", "open\n", mode="0666")
    run_as("score01", f"mkdir {directory}")
    install_ruling("dir.result", "score01", "irrelevant\n")

    assert chief_seal("../escape", check=False).returncode != 0
    assert chief_seal("protected.result", check=False).returncode != 0
    assert chief_seal("link.result", check=False).returncode != 0
    assert chief_seal("loose.result", check=False).returncode != 0
    assert chief_seal("dir.result", check=False).returncode != 0

    assert protected.read_text(encoding="utf-8") == "official\n"
    assert collision.read_text(encoding="utf-8") == "attacker\n"
    assert symlink.is_symlink()
    assert loose.read_text(encoding="utf-8") == "open\n"
    assert directory.is_dir()
    after_audit = audit.read_text(encoding="utf-8")
    assert after_audit.startswith(before_audit)
    assert "action=rejected artifact=INVALID_NAME reason=invalid-basename" in after_audit
    assert "action=rejected artifact=protected.result reason=destination-exists" in after_audit
    assert "action=rejected artifact=link.result reason=source-is-symlink" in after_audit
    assert "action=rejected artifact=loose.result reason=source-other-writable" in after_audit
    assert "action=rejected artifact=dir.result reason=source-not-regular-file" in after_audit


def test_lyra_seal_rejects_hardlinks_missing_names_and_wrong_owner():
    """Less obvious rejected inputs are audited and leave incoming/sealed state intact."""
    audit = Path("/srv/lyra/audit/seal.log")
    before_audit = audit.read_text(encoding="utf-8") if audit.exists() else ""
    hard = Path("/srv/lyra/incoming/hardlink.result")
    hard_peer = Path("/srv/lyra/incoming/hardlink-peer.result")
    missing = Path("/srv/lyra/incoming/missing.result")
    missing_dst = Path("/srv/lyra/sealed/missing.result")
    wrong_owner = Path("/srv/lyra/incoming/root-owned.result")
    wrong_dst = Path("/srv/lyra/sealed/root-owned.result")

    for path in [hard, hard_peer, missing, missing_dst, wrong_owner, wrong_dst]:
        path.unlink(missing_ok=True)

    stage_artifact("score01", "hardlink.result", "linked\n")
    subprocess.run(["ln", str(hard), str(hard_peer)], check=True)
    install_ruling("missing.result", "score01", "missing\n")
    wrong_owner.write_text("root placed\n", encoding="utf-8")
    subprocess.run(["chown", "root:lyra-score", str(wrong_owner)], check=True)
    subprocess.run(["chmod", "0640", str(wrong_owner)], check=True)
    install_ruling("root-owned.result", "score01", "root placed\n")

    assert chief_seal(".", check=False).returncode != 0
    assert chief_seal("..", check=False).returncode != 0
    assert chief_seal("missing.result", check=False).returncode != 0
    assert chief_seal("hardlink.result", check=False).returncode != 0
    assert chief_seal("root-owned.result", check=False).returncode != 0

    assert hard.read_text(encoding="utf-8") == "linked\n"
    assert hard_peer.read_text(encoding="utf-8") == "linked\n"
    assert hard.stat().st_ino == hard_peer.stat().st_ino
    assert not missing.exists()
    assert not missing_dst.exists()
    assert wrong_owner.read_text(encoding="utf-8") == "root placed\n"
    assert not wrong_dst.exists()

    after_audit = audit.read_text(encoding="utf-8")
    assert after_audit.startswith(before_audit)
    assert after_audit.count("action=rejected artifact=INVALID_NAME reason=invalid-basename") >= 2
    assert "action=rejected artifact=missing.result reason=source-missing" in after_audit
    assert "action=rejected artifact=hardlink.result reason=source-has-hardlinks" in after_audit
    assert "action=rejected artifact=root-owned.result reason=source-wrong-owner" in after_audit


def test_lyra_seal_repairs_audit_parent_before_appending():
    """The seal path preserves audit integrity even if the audit directory drifted."""
    audit_dir = Path("/srv/lyra/audit")
    audit = audit_dir / "seal.log"
    name = "audit-parent.result"
    src = Path("/srv/lyra/incoming") / name
    dst = Path("/srv/lyra/sealed") / name
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)

    subprocess.run(["chown", "root:root", str(audit_dir)], check=True)
    subprocess.run(["chmod", "0777", str(audit_dir)], check=True)
    stage_artifact("score01", name, "parent-check\n")
    chief_seal(name)

    audit_dir_st = audit_dir.stat()
    assert pwd.getpwuid(audit_dir_st.st_uid).pw_name == "root"
    assert grp.getgrgid(audit_dir_st.st_gid).gr_name == "lyra-audit"
    assert mode(audit_dir) == 0o750
    audit_st = audit.stat()
    assert pwd.getpwuid(audit_st.st_uid).pw_name == "root"
    assert grp.getgrgid(audit_st.st_gid).gr_name == "lyra-audit"
    assert mode(audit) == 0o640
    digest = hashlib.sha256(b"parent-check\n").hexdigest()
    assert_audit_event(audit.read_text(), "sealed", name, f"sha256={digest}")


def test_lyra_seal_sudo_access_is_limited_to_chiefs():
    """The sealing path is operationally available to chiefs, not score-only users."""
    name = "sudo-scope.result"
    src = Path("/srv/lyra/incoming") / name
    dst = Path("/srv/lyra/sealed") / name
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)

    stage_artifact("score01", name, "scope\n")
    assert run_as("score01", f"sudo -n /usr/local/sbin/lyra-seal {name}", check=False).returncode != 0
    assert src.exists()
    chief_seal(name)
    assert dst.exists()


def test_ssh_pam_tmpfiles_sysctl_and_logrotate_policy_files():
    """The login, session-limit, persistence, kernel, and audit rotation policies match."""
    ssh_path = Path("/etc/ssh/sshd_config.d/80-lyra-matchroom.conf")
    assert ssh_path.exists()
    ssh_lines = meaningful_lines(ssh_path)
    assert "Match Group lyra-audit" in ssh_lines
    match_index = ssh_lines.index("Match Group lyra-audit")
    global_lines = ssh_lines[:match_index]
    match_lines = ssh_lines[match_index + 1 :]
    require_setting(global_lines, "PermitRootLogin", "PermitRootLogin no")
    require_setting(global_lines, "PasswordAuthentication", "PasswordAuthentication no")
    require_setting(global_lines, "KbdInteractiveAuthentication", "KbdInteractiveAuthentication no")
    require_setting(global_lines, "AllowGroups", "AllowGroups lyra-score lyra-chief lyra-audit")
    require_setting(match_lines, "PermitTTY", "PermitTTY no")
    require_setting(match_lines, "AllowTcpForwarding", "AllowTcpForwarding no")
    require_setting(match_lines, "X11Forwarding", "X11Forwarding no")
    subprocess.run(["sshd", "-t"], check=True)

    limits = [line.split() for line in meaningful_lines("/etc/security/limits.d/60-lyra-matchroom.conf")]
    expected_limits = {
        "@lyra-score": ["@lyra-score", "hard", "maxlogins", "2"],
        "@lyra-chief": ["@lyra-chief", "hard", "maxlogins", "1"],
        "@lyra-audit": ["@lyra-audit", "hard", "maxlogins", "1"],
    }
    for subject, expected in expected_limits.items():
        matching = [line for line in limits if line[:1] == [subject] and line[2:3] == ["maxlogins"]]
        assert expected in matching
        assert all(line == expected for line in matching)

    tmpfiles = [line.split() for line in meaningful_lines("/etc/tmpfiles.d/lyra-matchroom.conf")]
    expected_tmpfiles = [
        ["d", "/srv/lyra/incoming", "2770", "root", "lyra-score"],
        ["d", "/srv/lyra/sealed", "2750", "root", "lyra-chief"],
        ["d", "/srv/lyra/rulings", "2770", "root", "lyra-chief"],
        ["d", "/srv/lyra/audit", "0750", "root", "lyra-audit"],
    ]
    assert len(tmpfiles) == len(expected_tmpfiles)
    for actual, expected in zip(tmpfiles, expected_tmpfiles):
        assert actual[:5] == expected
        assert 5 <= len(actual) <= 7
        assert all(field == "-" for field in actual[5:])

    sysctl_lines = [re.split(r"\s*=\s*", line) for line in meaningful_lines("/etc/sysctl.d/90-lyra-matchroom.conf")]
    expected_sysctl = {
        "fs.protected_hardlinks": "1",
        "fs.protected_symlinks": "1",
        "kernel.yama.ptrace_scope": "1",
    }
    for key, value in expected_sysctl.items():
        matching = [line for line in sysctl_lines if line[0] == key]
        assert [key, value] in matching
        assert all(line == [key, value] for line in matching)

    logrotate = Path("/etc/logrotate.d/lyra-matchroom")
    assert logrotate.exists()
    assert mode(logrotate) == 0o644
    logrotate_lines = meaningful_lines(logrotate)
    assert logrotate_lines[0] == "/srv/lyra/audit/seal.log /srv/lyra/audit/seal-ledger.tsv {"
    assert logrotate_lines[-1] == "}"
    expected_directives = {
        "weekly",
        "rotate 12",
        "missingok",
        "notifempty",
        "compress",
        "create 0640 root lyra-audit",
        "su root lyra-audit",
    }
    assert set(logrotate_lines[1:-1]) == expected_directives
    assert len(logrotate_lines[1:-1]) == len(expected_directives)
    subprocess.run(["logrotate", "-d", str(logrotate)], check=True)


def test_ruling_and_ledger_contract_are_enforced():
    """Sealing depends on a valid ruling and maintains a serialized hash-chain ledger."""
    ledger = Path("/srv/lyra/audit/seal-ledger.tsv")
    missing = Path("/srv/lyra/incoming/no-ruling.result")
    expired = Path("/srv/lyra/incoming/expired.result")
    mismatch = Path("/srv/lyra/incoming/mismatch.result")
    valid = Path("/srv/lyra/incoming/ledger.result")
    for path in [missing, expired, mismatch, valid, Path("/srv/lyra/sealed/ledger.result")]:
        path.unlink(missing_ok=True)

    missing.write_text("unapproved\n", encoding="utf-8")
    subprocess.run(["chown", "score01:lyra-score", str(missing)], check=True)
    assert chief_seal("no-ruling.result", check=False).returncode != 0

    stage_artifact("score01", "expired.result", "old\n", expires="2000-01-01T00:00:00Z")
    assert chief_seal("expired.result", check=False).returncode != 0

    mismatch.write_text("actual\n", encoding="utf-8")
    subprocess.run(["chown", "score01:lyra-score", str(mismatch)], check=True)
    subprocess.run(["chmod", "0640", str(mismatch)], check=True)
    install_ruling("mismatch.result", "score01", "expected\n")
    assert chief_seal("mismatch.result", check=False).returncode != 0

    before_rows = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    expected_hash = stage_artifact("score01", "ledger.result", "ledger-payload\n", nonce="abcdef0123456789")
    chief_seal("ledger.result")
    rows = ledger.read_text(encoding="utf-8").splitlines()
    assert len(rows) == len(before_rows) + 1
    fields = rows[-1].split("\t")
    assert len(fields) == 8
    assert fields[0] == str(len(rows))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fields[1])
    assert fields[2:6] == ["ledger.result", "score01", expected_hash, "abcdef0123456789"]
    assert fields[6] == (before_rows[-1].split("\t")[7] if before_rows else "0" * 64)
    assert fields[7] == hashlib.sha256("\t".join(fields[:7]).encode()).hexdigest()
    st = ledger.stat()
    assert pwd.getpwuid(st.st_uid).pw_name == "root"
    assert grp.getgrgid(st.st_gid).gr_name == "lyra-audit"
    assert mode(ledger) == 0o640
def test_runtime_access_examples_match_policy():
    """Representative file writes follow the configured group and ACL policy."""
    sample = Path("/srv/lyra/incoming/check.txt")
    subprocess.run(["runuser", "-u", "score01", "--", "bash", "-lc", f"touch {sample}"], check=True)
    st = sample.stat()
    assert pwd.getpwuid(st.st_uid).pw_name == "score01"
    assert grp.getgrgid(st.st_gid).gr_name == "lyra-score"
    acl = subprocess.check_output(["getfacl", "-cp", str(sample)], text=True)
    assert any(line.startswith("group:lyra-chief:rwx") for line in acl.splitlines())

    denied = subprocess.run(
        ["runuser", "-u", "guest01", "--", "bash", "-lc", "touch /srv/lyra/incoming/guest.txt"],
        check=False,
    )
    assert denied.returncode != 0
