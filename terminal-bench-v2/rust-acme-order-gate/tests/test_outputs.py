import base64
import hashlib
import json
import subprocess
from pathlib import Path

APP = Path("/app/acmegate")
BIN = APP / "bin" / "acmegate"
NOW = 1_700_000_000


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def protected_json(account: str, nonce: str, order: str) -> str:
    return f'{{"alg":"ACME-MAC64","kid":"{account}","nonce":"{nonce}","url":"https://ca.local/acme/order/{order}"}}'


def payload_json(account: str, challenge: str, order: str) -> str:
    return f'{{"account":"{account}","challenge":"{challenge}","order":"{order}","status":"ready"}}'


def mac64(key: str, protected: str, payload: str) -> str:
    h = 14695981039346656037
    for byte in (key + "\n" + protected + "." + payload).encode():
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def record(
    order: str,
    account: str,
    nonce: str,
    challenge: str,
    key: str,
    *,
    protected_text=None,
    payload_text=None,
    signature=None,
) -> str:
    ptxt = (
        protected_text
        if protected_text is not None
        else protected_json(account, nonce, order)
    )
    ytxt = (
        payload_text
        if payload_text is not None
        else payload_json(account, challenge, order)
    )
    protected = b64url(ptxt)
    payload = b64url(ytxt)
    sig = signature if signature is not None else mac64(key, protected, payload)
    return f"{order}\t{account}\t{nonce}\t{challenge}\t{protected}\t{payload}\t{sig}"


def write_case(
    root: Path, *, trust: str, state: str, rows: list[str]
) -> tuple[Path, Path, Path]:
    trust_path = root / "trust.tsv"
    state_path = root / "state.tsv"
    bundle = root / "bundle"
    bundle.mkdir(parents=True)
    trust_path.write_text(trust, encoding="utf-8")
    state_path.write_text(state, encoding="utf-8")
    (bundle / "evidence.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return trust_path, state_path, bundle


def run_gate(trust: Path, state: Path, bundle: Path, out: Path, now: int = NOW) -> dict:
    subprocess.run(
        [
            str(BIN),
            "--trust",
            str(trust),
            "--state",
            str(state),
            "--bundle",
            str(bundle),
            "--out",
            str(out),
            "--now",
            str(now),
        ],
        check=True,
        cwd="/app",
        text=True,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def digest_tree(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    h.update(str(child.relative_to(path)).encode())
                    h.update(child.read_bytes())
        else:
            h.update(path.name.encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def base_trust() -> str:
    return (
        "acct-a\tkey-alpha\tactive\n"
        "acct-b\tkey-bravo\tactive\n"
        "acct-z\tkey-zulu\trevoked\n"
    )


def base_state() -> str:
    return "\n".join(
        [
            "nonce\tn-used",
            f"order\tord-a\tacct-a\tpending\t{NOW + 500}",
            f"order\tord-b\tacct-a\tpending\t{NOW + 700}",
            f"order\tord-c\tacct-a\tpending\t{NOW + 900}",
            f"order\tord-d\tacct-a\tpending\t{NOW + 900}",
            f"order\tord-exp\tacct-a\tpending\t{NOW - 1}",
            f"order\tord-valid\tacct-a\tvalid\t{NOW + 900}",
            f"order\tord-other\tacct-b\tpending\t{NOW + 900}",
            f"challenge\tchal-a\tacct-a\ttok-a\t{NOW - 100}\t{NOW + 100}\tvalid",
            f"challenge\tchal-b\tacct-a\ttok-b\t{NOW - 50}\t{NOW + 100}\tvalid",
            f"challenge\tchal-exp\tacct-a\ttok-e\t{NOW - 500}\t{NOW - 10}\tvalid",
            f"challenge\tchal-future\tacct-a\ttok-f\t{NOW + 10}\t{NOW + 300}\tvalid",
            f"challenge\tchal-other\tacct-b\ttok-o\t{NOW - 50}\t{NOW + 100}\tvalid",
            f"challenge\tchal-pending\tacct-a\ttok-p\t{NOW - 50}\t{NOW + 100}\tpending",
            "",
        ]
    )


def test_valid_orders_are_admitted_with_deterministic_state(tmp_path):
    """Canonical active-account orders are admitted and summarized deterministically."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    trust, state, bundle = write_case(
        tmp_path,
        trust=base_trust(),
        state=base_state(),
        rows=[
            record("ord-b", "acct-a", "n-b", "chal-b", "key-alpha"),
            record("ord-a", "acct-a", "n-a", "chal-a", "key-alpha"),
        ],
    )
    report = run_gate(trust, state, bundle, tmp_path / "nested" / "report.json")
    assert report == {
        "accepted": True,
        "records": [
            {
                "account": "acct-a",
                "decision": "ACCEPT",
                "line": 1,
                "nonce": "n-b",
                "order": "ord-b",
                "reasons": [],
            },
            {
                "account": "acct-a",
                "decision": "ACCEPT",
                "line": 2,
                "nonce": "n-a",
                "order": "ord-a",
                "reasons": [],
            },
        ],
        "state": {"new_nonces": ["n-a", "n-b"], "ready_orders": ["ord-a", "ord-b"]},
        "summary": {"accepted": 2, "rejected": 0},
    }


def test_canonical_jws_bytes_and_signature_are_enforced(tmp_path):
    """Canonical JSON bytes and unpadded base64url are checked independently from the signature."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    wrong_protected = '{"kid":"acct-a","alg":"ACME-MAC64","nonce":"n-canon","url":"https://ca.local/acme/order/ord-a"}'
    wrong_payload = '{ "status":"ready", "order":"ord-b", "challenge":"chal-b", "account":"acct-a" }'
    padded_protected = b64url(protected_json("acct-a", "n-pad-prot", "ord-c")) + "="
    padded_protected_payload = b64url(payload_json("acct-a", "chal-a", "ord-c"))
    padded_payload_protected = b64url(protected_json("acct-a", "n-pad-pay", "ord-d"))
    padded_payload = b64url(payload_json("acct-a", "chal-b", "ord-d")) + "="
    trust, state, bundle = write_case(
        tmp_path,
        trust=base_trust(),
        state=base_state(),
        rows=[
            record(
                "ord-a",
                "acct-a",
                "n-canon",
                "chal-a",
                "key-alpha",
                protected_text=wrong_protected,
            ),
            record(
                "ord-b",
                "acct-a",
                "n-payload",
                "chal-b",
                "key-alpha",
                payload_text=wrong_payload,
            ),
            "\t".join(
                [
                    "ord-c",
                    "acct-a",
                    "n-pad-prot",
                    "chal-a",
                    padded_protected,
                    padded_protected_payload,
                    mac64("key-alpha", padded_protected, padded_protected_payload),
                ]
            ),
            "\t".join(
                [
                    "ord-d",
                    "acct-a",
                    "n-pad-pay",
                    "chal-b",
                    padded_payload_protected,
                    padded_payload,
                    mac64("key-alpha", padded_payload_protected, padded_payload),
                ]
            ),
            record(
                "ord-c",
                "acct-a",
                "n-sig",
                "chal-a",
                "key-alpha",
                signature="0000000000000000",
            ),
        ],
    )
    report = run_gate(trust, state, bundle, tmp_path / "report.json")
    assert report["accepted"] is False
    assert [row["reasons"] for row in report["records"]] == [
        ["BAD_PROTECTED"],
        ["BAD_PAYLOAD"],
        ["BAD_PROTECTED"],
        ["BAD_PAYLOAD"],
        ["BAD_SIGNATURE"],
    ]
    assert report["summary"] == {"accepted": 0, "rejected": 5}
    assert report["state"] == {"new_nonces": [], "ready_orders": []}


def test_replay_order_and_challenge_state_all_affect_decisions(tmp_path):
    """Replay state, order state, and challenge authorization each reject same-schema evidence."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    trust, state, bundle = write_case(
        tmp_path,
        trust=base_trust(),
        state=base_state(),
        rows=[
            record("ord-a", "acct-a", "n-ok", "chal-a", "key-alpha"),
            record("ord-b", "acct-a", "n-ok", "chal-b", "key-alpha"),
            record("ord-c", "acct-a", "n-used", "chal-a", "key-alpha"),
            record("ord-valid", "acct-a", "n-valid", "chal-a", "key-alpha"),
            record("ord-exp", "acct-a", "n-exp", "chal-a", "key-alpha"),
            record("ord-other", "acct-a", "n-other", "chal-a", "key-alpha"),
            record("ord-c", "acct-a", "n-ch-exp", "chal-exp", "key-alpha"),
            record("ord-c", "acct-a", "n-ch-future", "chal-future", "key-alpha"),
            record("ord-c", "acct-a", "n-ch-other", "chal-other", "key-alpha"),
            record("ord-c", "acct-a", "n-ch-pending", "chal-pending", "key-alpha"),
        ],
    )
    report = run_gate(trust, state, bundle, tmp_path / "report.json")
    assert [row["decision"] for row in report["records"]] == ["ACCEPT"] + ["REJECT"] * 9
    assert [row["reasons"] for row in report["records"]] == [
        [],
        ["NONCE_REPLAY"],
        ["NONCE_REPLAY"],
        ["ORDER_STATE"],
        ["ORDER_STATE"],
        ["ORDER_STATE"],
        ["CHALLENGE_TIME"],
        ["CHALLENGE_TIME"],
        ["CHALLENGE_BINDING"],
        ["CHALLENGE_BINDING"],
    ]
    assert report["state"] == {"new_nonces": ["n-ok"], "ready_orders": ["ord-a"]}


def test_inactive_accounts_and_malformed_layers_do_not_hide_other_reasons(tmp_path):
    """All applicable reason codes are emitted in the documented order for a bad record."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    wrong_payload = b64url(
        '{"account":"acct-z","challenge":"missing-chal","order":"missing-order","status":"pending"}'
    )
    trust, state, bundle = write_case(
        tmp_path,
        trust=base_trust(),
        state=base_state(),
        rows=[
            (
                f"missing-order\tacct-z\tn-used\tmissing-chal\tnot_base64=\t"
                f"{wrong_payload}\tffffffffffffffff"
            )
        ],
    )
    report = run_gate(trust, state, bundle, tmp_path / "report.json")
    assert report["records"][0] == {
        "account": "acct-z",
        "decision": "REJECT",
        "line": 1,
        "nonce": "n-used",
        "order": "missing-order",
        "reasons": [
            "BAD_ACCOUNT",
            "BAD_PROTECTED",
            "BAD_PAYLOAD",
            "BAD_SIGNATURE",
            "NONCE_REPLAY",
            "ORDER_STATE",
            "CHALLENGE_BINDING",
            "CHALLENGE_TIME",
        ],
    }
    assert report["accepted"] is False


def test_inputs_are_preserved_and_reports_repeat(tmp_path):
    """Admission runs do not mutate evidence inputs and produce byte-stable JSON."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    trust, state, bundle = write_case(
        tmp_path,
        trust=base_trust(),
        state=base_state(),
        rows=[record("ord-a", "acct-a", "n-repeat", "chal-a", "key-alpha")],
    )
    before = digest_tree([trust, state, bundle])
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_report = run_gate(trust, state, bundle, first)
    second_report = run_gate(trust, state, bundle, second)
    assert first_report == second_report
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert digest_tree([trust, state, bundle]) == before


def test_trust_replacement_statuses_and_ignored_lines_are_honored(tmp_path):
    """Trust, state, and evidence ignore comments and blanks while applying final account rows."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    trust, state, bundle = write_case(
        tmp_path,
        trust=(
            "# archived account list\n"
            "\n"
            "acct-dup\told-key\tsuspended\n"
            "acct-hold\tkey-hold\tstaged\n"
            "acct-dup\tkey-dup\tactive\n"
        ),
        state="\n".join(
            [
                "# carried replay state",
                "",
                f"order\tord-dup\tacct-dup\tpending\t{NOW + 30}",
                f"order\tord-hold\tacct-hold\tpending\t{NOW + 30}",
                f"challenge\tchal-dup\tacct-dup\ttok-d\t{NOW - 1}\t{NOW + 30}\tvalid",
                "",
                f"challenge\tchal-hold\tacct-hold\ttok-h\t{NOW - 1}\t{NOW + 30}\tvalid",
                "",
            ]
        ),
        rows=[
            "# skipped evidence comment",
            "",
            record("ord-dup", "acct-dup", "n-dup", "chal-dup", "key-dup"),
            "# second skipped evidence comment",
            record("ord-hold", "acct-hold", "n-hold", "chal-hold", "key-hold"),
        ],
    )
    report = run_gate(trust, state, bundle, tmp_path / "report.json")
    assert report["records"] == [
        {
            "account": "acct-dup",
            "decision": "ACCEPT",
            "line": 3,
            "nonce": "n-dup",
            "order": "ord-dup",
            "reasons": [],
        },
        {
            "account": "acct-hold",
            "decision": "REJECT",
            "line": 5,
            "nonce": "n-hold",
            "order": "ord-hold",
            "reasons": ["BAD_ACCOUNT"],
        },
    ]
    assert report["accepted"] is False
    assert report["state"] == {"new_nonces": ["n-dup"], "ready_orders": ["ord-dup"]}
    assert report["summary"] == {"accepted": 1, "rejected": 1}


def test_order_and_challenge_time_bounds_are_inclusive(tmp_path):
    """Order expiry and both challenge time boundaries admit records at exactly now."""
    subprocess.run(
        [str(APP / "scripts" / "build.sh")], check=True, cwd="/app", text=True
    )
    trust, state, bundle = write_case(
        tmp_path,
        trust=base_trust(),
        state="\n".join(
            [
                f"order\tord-order-edge\tacct-a\tpending\t{NOW}",
                f"order\tord-chal-start\tacct-a\tpending\t{NOW + 50}",
                f"order\tord-chal-end\tacct-a\tpending\t{NOW + 50}",
                f"challenge\tchal-start\tacct-a\ttok-s\t{NOW}\t{NOW + 50}\tvalid",
                f"challenge\tchal-end\tacct-a\ttok-e\t{NOW - 50}\t{NOW}\tvalid",
                "",
            ]
        ),
        rows=[
            record(
                "ord-order-edge", "acct-a", "n-order-edge", "chal-start", "key-alpha"
            ),
            record(
                "ord-chal-start", "acct-a", "n-chal-start", "chal-start", "key-alpha"
            ),
            record("ord-chal-end", "acct-a", "n-chal-end", "chal-end", "key-alpha"),
        ],
    )
    report = run_gate(trust, state, bundle, tmp_path / "report.json")
    assert [row["decision"] for row in report["records"]] == [
        "ACCEPT",
        "ACCEPT",
        "ACCEPT",
    ]
    assert [row["reasons"] for row in report["records"]] == [[], [], []]
    assert report["accepted"] is True
    assert report["state"] == {
        "new_nonces": ["n-chal-end", "n-chal-start", "n-order-edge"],
        "ready_orders": ["ord-chal-end", "ord-chal-start", "ord-order-edge"],
    }
    assert report["summary"] == {"accepted": 3, "rejected": 0}
