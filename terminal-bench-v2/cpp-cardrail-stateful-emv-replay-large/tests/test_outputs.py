from __future__ import annotations
import csv
import hashlib
import json
import subprocess
from pathlib import Path

APP = Path("/app/cardrail")
BIN = APP / "bin/cardrail_replay"


def split_line(line: str):
    parts = line.rstrip("\n").split("|")
    if len(parts) < 16:
        raise AssertionError(f"bad trace line: {line!r}")
    keys = [
        "record",
        "batch",
        "session",
        "card",
        "terminal",
        "atc",
        "kind",
        "pan",
        "amount",
        "currency",
        "cid",
        "host",
        "sw",
        "tlv",
        "script",
        "seq",
    ]
    return dict(zip(keys, parts))


def read_kv(path: Path):
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def parse_bool(value: str, default: bool = False):
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return default


def parse_array(value: str):
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    items = []
    for raw in value[1:-1].split(","):
        item = raw.strip().strip('"')
        if item:
            items.append(item)
    return items


def parse_tags(hexstr: str):
    data = bytes.fromhex(hexstr) if hexstr else b""
    tags = set()

    def rec(pos, end):
        while pos < end:
            start = pos
            if pos >= len(data):
                break
            tag = [data[pos]]
            pos += 1
            if tag[0] & 0x1F == 0x1F:
                while pos < end and pos < len(data):
                    b = data[pos]
                    pos += 1
                    tag.append(b)
                    if not (b & 0x80):
                        break
            if pos >= end or pos >= len(data):
                break
            lb = data[pos]
            pos += 1
            if lb & 0x80:
                n = lb & 0x7F
                ln = 0
                for _ in range(n):
                    if pos < end and pos < len(data):
                        ln = (ln << 8) | data[pos]
                        pos += 1
            else:
                ln = lb
            _val_start, val_end = pos, min(end, pos + ln)
            tag_hex = "".join(f"{b:02X}" for b in tag)
            if tag_hex.startswith("DF"):
                tags.add(tag_hex)
            if tag[0] & 0x20:
                rec(pos, val_end)
            pos = val_end
            if pos <= start:
                break
        return pos

    rec(0, len(data))
    return sorted(tags)


def atcnum(v):
    return int(v, 16)


def is_cont(sw):
    return sw.startswith("61") or sw.startswith("6C")


def is_success(sw):
    return sw in {"9000", "9100"}


def script_status(sw, profile):
    if is_success(sw):
        return "applied"
    if sw in profile["soft_script_status"]:
        return "failed_soft"
    if sw in profile["hard_script_status"]:
        return "failed_hard"
    return "failed_hard"


def selected_profile(manifest):
    for p in [
        Path("/etc/cardrail/profile.toml"),
        APP / "profiles" / f"{manifest.get('profile', 'default')}.toml",
        APP / "profiles/default.toml",
    ]:
        if p.exists():
            data = read_kv(p)
            if data.get("profile_id"):
                return {
                    "profile_id": data["profile_id"],
                    "allow_private_tags": parse_bool(
                        data.get("allow_private_tags", "false")
                    ),
                    "soft_script_status": set(
                        parse_array(data.get("soft_script_status", "[]"))
                    ),
                    "hard_script_status": set(
                        parse_array(data.get("hard_script_status", "[]"))
                    ),
                }
    return {
        "profile_id": manifest.get("profile", "default"),
        "allow_private_tags": False,
        "soft_script_status": set(),
        "hard_script_status": set(),
    }


def reference(case_dir: Path):
    manifest = read_kv(case_dir / "manifest.meta")
    case_id = manifest["case_id"]
    profile = selected_profile(manifest)
    rows = [
        split_line(line)
        for line in (case_dir / "trace.tsv").read_text().splitlines()[1:]
        if line.strip()
    ]
    rows.sort(
        key=lambda r: (r["card"], r["session"], r["atc"], int(r["seq"]), r["record"])
    )
    first_atc = {}
    for r in rows:
        if r["kind"] == "TXN" and r["card"] not in first_atc:
            first_atc[r["card"]] = r["atc"]

    def lineage(r):
        return f"{r['card']}:{first_atc[r['card']]}"

    txs, scripts = [], []
    seen_tx, seen_script = set(), set()
    replayed_tx = set()
    tx_seen = scripts_seen = 0
    for i, r in enumerate(rows):
        if r["kind"] == "TXN":
            tx_seen += 1
            key = (lineage(r), r["atc"])
            if key in seen_tx:
                replayed_tx.add(key)
                continue
            seen_tx.add(key)
            rr = dict(r)
            eff = rr["sw"]
            if is_cont(eff):
                for nxt in rows[i + 1 :]:
                    if (
                        nxt["kind"] == "TXN"
                        and nxt["card"] == r["card"]
                        and nxt["session"] == r["session"]
                        and nxt["atc"] == r["atc"]
                    ):
                        eff = nxt["sw"]
                        rr["tlv"] += nxt["tlv"]
                        break
            txs.append((rr, parse_tags(rr["tlv"]), eff))
        elif r["kind"] == "SCRIPT":
            scripts_seen += 1
            key = (lineage(r), r["atc"], r["script"])
            if key in seen_script:
                continue
            seen_script.add(key)
            scripts.append((r, parse_tags(r["tlv"]), script_status(r["sw"], profile)))
    txs.sort(
        key=lambda x: (
            x[0]["card"],
            first_atc[x[0]["card"]],
            atcnum(x[0]["atc"]),
            x[0]["session"],
            x[0]["record"],
        )
    )
    scripts.sort(
        key=lambda x: (
            x[0]["card"],
            first_atc[x[0]["card"]],
            atcnum(x[0]["atc"]),
            x[0]["script"],
            x[0]["record"],
        )
    )
    sessions = {}

    def sess_for(r):
        return sessions.setdefault(
            r["session"],
            {
                "session_id": r["session"],
                "card_id": r["card"],
                "lineage_key": lineage(r),
                "terminal_ids": set(),
                "first_atc": r["atc"],
                "last_atc": r["atc"],
                "approved": 0,
                "declined": 0,
                "scripts_applied": 0,
                "scripts_failed_soft": 0,
                "scripts_failed_hard": 0,
                "reversal_required": 0,
                "private_tags": set(),
            },
        )

    for r, tags, eff in txs:
        s = sess_for(r)
        s["terminal_ids"].add(r["terminal"])
        if profile["allow_private_tags"]:
            s["private_tags"].update(tags)
        s["first_atc"] = min(s["first_atc"], r["atc"])
        s["last_atc"] = max(s["last_atc"], r["atc"])
        if r["host"] == "approved":
            s["approved"] += 1
        else:
            s["declined"] += 1
    for r, tags, status in scripts:
        s = sess_for(r)
        s["terminal_ids"].add(r["terminal"])
        if profile["allow_private_tags"]:
            s["private_tags"].update(tags)
        if status == "applied":
            s["scripts_applied"] += 1
        elif status == "failed_soft":
            s["scripts_failed_soft"] += 1
        else:
            s["scripts_failed_hard"] += 1
    hard = {
        (lineage(r), r["atc"]): "issuer_script_hard_failure"
        for r, tags, status in scripts
        if status == "failed_hard"
    }
    reversals = []
    for r, tags, eff in txs:
        reason = hard.get((lineage(r), r["atc"]))
        if not reason and r["cid"] == "offline_approved" and r["host"] == "declined":
            reason = "host_decline_after_offline_approval"
        if not reason and r["cid"] == "online_approved" and r["host"] == "timeout":
            reason = "host_timeout_after_online_approval"
        if not reason and (lineage(r), r["atc"]) in replayed_tx:
            reason = "duplicate_atc_replay_attempt"
        if reason:
            reversals.append(
                [
                    case_id,
                    r["session"],
                    r["card"],
                    r["atc"],
                    reason,
                    r["amount"],
                    r["currency"],
                ]
            )
            sessions[r["session"]]["reversal_required"] += 1
    reversals.sort(key=lambda r: (r[2], r[3], r[4], r[1]))
    sess_rows = []
    for s in sessions.values():
        s = dict(s)
        s["terminal_ids"] = sorted(s["terminal_ids"])
        s["private_tags"] = sorted(s["private_tags"])
        sess_rows.append(s)
    sess_rows.sort(key=lambda s: (s["card_id"], s["lineage_key"], s["session_id"]))
    script_rows = [
        {
            "case_id": case_id,
            "session_id": r["session"],
            "card_id": r["card"],
            "lineage_key": lineage(r),
            "script_id": r["script"],
            "atc": r["atc"],
            "status": status,
            "terminal_id": r["terminal"],
            "private_tags": tags if profile["allow_private_tags"] else [],
        }
        for r, tags, status in scripts
    ]
    totals = {
        "transactions_seen": tx_seen,
        "transactions_kept": len(txs),
        "scripts_seen": scripts_seen,
        "scripts_kept": len(scripts),
        "scripts_applied": sum(1 for _, _, s in scripts if s == "applied"),
        "scripts_failed_soft": sum(1 for _, _, s in scripts if s == "failed_soft"),
        "scripts_failed_hard": sum(1 for _, _, s in scripts if s == "failed_hard"),
        "reversals_queued": len(reversals),
    }
    summary = {
        "schema_version": 1,
        "case_id": case_id,
        "profile_id": profile["profile_id"],
        "totals": totals,
        "sessions": sess_rows,
    }
    stream = f"summary|{case_id}|{profile['profile_id']}|{len(txs)}|{len(scripts)}|{len(reversals)}\n"
    for s in sess_rows:
        stream += "session|{session_id}|{card_id}|{lineage_key}|{first_atc}|{last_atc}|{approved}|{declined}|{scripts_applied}|{scripts_failed_soft}|{scripts_failed_hard}|{reversal_required}|{tags}\n".format(
            **s, tags=",".join(s["private_tags"])
        )
    for r in script_rows:
        stream += f"script|{r['session_id']}|{r['card_id']}|{r['lineage_key']}|{r['script_id']}|{r['atc']}|{r['status']}|{','.join(r['private_tags'])}\n"
    for r in reversals:
        stream += f"reversal|{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5]}|{r[6]}\n"
    h = 1469598103934665603
    for b in stream.encode():
        h ^= b
        h = (h * 1099511628211) & ((1 << 64) - 1)
    return summary, script_rows, reversals, f"cardrail-v1 {h:016x}\n"


def expected_ledger_rows(case_dir: Path):
    manifest = read_kv(case_dir / "manifest.meta")
    case_id = manifest["case_id"]
    rows = [
        split_line(line)
        for line in (case_dir / "trace.tsv").read_text().splitlines()[1:]
        if line.strip()
    ]
    rows.sort(
        key=lambda r: (r["card"], r["session"], r["atc"], int(r["seq"]), r["record"])
    )
    first_atc = {}
    for r in rows:
        if r["kind"] == "TXN" and r["card"] not in first_atc:
            first_atc[r["card"]] = r["atc"]

    def lineage(r):
        return f"{r['card']}:{first_atc[r['card']]}"

    tx_rows, script_rows = [], []
    seen_tx, seen_script = set(), set()
    for r in rows:
        if r["kind"] == "TXN":
            key = (lineage(r), r["atc"])
            if key in seen_tx:
                continue
            seen_tx.add(key)
            tx_rows.append(
                f"{case_id}\t{lineage(r)}\tTXN\t{r['atc']}\t{r['record']}"
            )
        elif r["kind"] == "SCRIPT":
            key = (lineage(r), r["atc"], r["script"])
            if key in seen_script:
                continue
            seen_script.add(key)
            script_rows.append(
                f"{case_id}\t{lineage(r)}\tSCRIPT\t{r['atc']}\t{r['script']}"
            )
    return tx_rows + script_rows


def assert_ledger_matches(case_dir: Path):
    ledger = (APP / "state/ledger.tsv").read_text().splitlines()
    expected = expected_ledger_rows(case_dir)
    assert len(ledger) == len(expected)
    assert set(ledger) == set(expected)


def run_case(case_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(BIN), str(case_dir), str(out_dir)], cwd=str(APP), check=True)


def read_outputs(out: Path):
    summary = json.loads((out / "case_summary.json").read_text())
    scripts = [
        json.loads(line)
        for line in (out / "issuer_scripts.jsonl").read_text().splitlines()
        if line
    ]
    with (out / "reversal_queue.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows and rows[0] == [
        "case_id",
        "session_id",
        "card_id",
        "atc",
        "reason",
        "amount",
        "currency",
    ]
    reversals = rows[1:]
    digest = (out / "audit_digest.txt").read_text()
    return summary, scripts, reversals, digest


def json_pairs(path: Path):
    return json.loads(path.read_text(), object_pairs_hook=lambda pairs: pairs)


def pair_keys(pairs):
    return [key for key, _value in pairs]


def pair_value(pairs, wanted):
    for key, value in pairs:
        if key == wanted:
            return value
    raise AssertionError(f"missing JSON key {wanted}")


def build_binary():
    subprocess.run([str(APP / "scripts/build.sh")], cwd=str(APP), check=True)


EXPECTED_HASHES = {
    "cases/regression/manifest.meta": "dfc8df81cf2cf180fa9be21b5d2df06e005092279c9717be6d3eccc81ace9537",
    "cases/regression/trace.tsv": "cc4f5e501a2a34e020c5147d74aa4e197531a87c320c177c9eba42a200b383b0",
    "cases/visible/README.note": "707e47fc4cc4e7a0c1c5c2dd1910338312f64c206c51d45040b521b0b1caba27",
    "cases/visible/manifest.meta": "371efc158cc1d84fbb7434e51f1bb0357123a63c22352ffc1dfe653cecceb060",
    "cases/visible/trace.tsv": "b2050478206c3fa58df5ab9dfb7ac539cbd2fbd9a2d2761d903e0837621eaef8",
    "reference/apdu-state.txt": "d008f31aa0c9f919a050511698b8e93aadf3034afaa8405bf834913afb0001fe",
    "reference/audit-digest.txt": "a8d66ffda324d8d3ec2199bc5d7d47a66f64e61b7853019128e175be840172d0",
    "reference/contract.txt": "e2443f15e3558bf9329d1c9d499d0819f52ee6d1d1ed4e433872c9bdc4d68baa",
    "reference/issuer-scripts.txt": "53dcd5edf33b8bb1055bdbebc2e2f8b01663a75ce28f391a26b2fea0b36852f8",
    "reference/output-artifacts.txt": "7e67a633a54479d5150bf35866e0f63e05d33d6ce858821df99da93d4e118f30",
    "reference/profile-selection.txt": "ce53b182af377ac97dcb50bf7ad9ae7ce7928bf1243aa5ecde665784c6c765f9",
    "reference/reversal-precedence.txt": "e022fef5ff0c55c33d5ef0e6eb83014f2e590478abd4bceb36a46a32e90a3514",
    "reference/tlv-profile.txt": "0248f6f4745177f2aa318ba82629087fbf0e520c4c438097d0163fe1cd2d0b54",
    "reference/threat-model.txt": "d0fcafc95662df0cc22ea8a7fd858497613c9310b0018476e8e9613ebaff868e",
    "profiles/default.toml": "da24e0f20bdf0932c1083e2ffd5706d3fff11d72e7855ca4b031f55970170f50",
    "profiles/fleet.toml": "739dedc0d53e4cc3cdc978f2837faf1913099fa1ca728e7ed0220f782455fdcf",
    "profiles/fragments/profile_fragment_001.toml": "9717ccca43340bc64735e94d1afc3f9e4ac37c347f1ce555dd9b2e8be06f9da9",
    "profiles/fragments/profile_fragment_002.toml": "6f012a8826af5aa84a8b1ccae8c498da761425d012a270ba749a31e1a70d9a0f",
    "profiles/fragments/profile_fragment_003.toml": "a9305c9051f16f46fbeac47c0f3905f2b7f707acc448a529a30402092ba6a7fa",
    "profiles/fragments/profile_fragment_004.toml": "89ce37fb8236c2c0f9a1cf6f4a17cc81eb431b3cbc4aa85c983310802f8272bb",
    "profiles/fragments/profile_fragment_005.toml": "08a111cb9be0615e95dce759af29b46948338f756dc5b77880d5382639d8620c",
    "profiles/fragments/profile_fragment_006.toml": "31250aa5858a361c184dfacb6f63189b92b9971e53ed9f3396435591b4164cb5",
    "profiles/fragments/profile_fragment_007.toml": "26c84c8bd56e70cd1a2f618252d270943cfb0f30fa9327a5d43f10bb442352df",
    "profiles/fragments/profile_fragment_008.toml": "a3cea55abbf5c15145f7deac68b8c94e40f12b69a974031ca489411ce49ce7a1",
    "profiles/fragments/profile_fragment_009.toml": "00d3cc98c085479ef9b20944bd43e78e85d07a468abc0594a0c4ad01dc75c97e",
    "profiles/fragments/profile_fragment_010.toml": "fff07a3f4d1d840b0c2454e551e4ef63e6936ce78e4ef97a04d25875c32c7a34",
    "profiles/fragments/profile_fragment_011.toml": "224554cc4623570c733e9f7de3ccc2645337f60f0ca15c8070e61c5e27a63c2c",
    "profiles/fragments/profile_fragment_012.toml": "980d4c79b53a1c4896130cd2ef40acb9a683f5ab0339ee84673d5a0e3e24068f",
    "profiles/fragments/profile_fragment_013.toml": "9420dca4536f550fbec1034d19de94f544b9d55fe3876925fa4887f599304330",
    "profiles/fragments/profile_fragment_014.toml": "b800d354d0249a101593e4378a8eeec0c199026430b1b6fc483f8d0023dad980",
    "profiles/fragments/profile_fragment_015.toml": "b773f7565ca4f16f98d19eecaf24b9598ea05aa64c4c1954e08b28f2c5ef10ab",
    "profiles/fragments/profile_fragment_016.toml": "cc5dab5f5b08b4f98229c6840ed616e1eeb888b49cc991efb9c6c141f247cdee",
    "profiles/fragments/profile_fragment_017.toml": "d217136de0766a48dc944a3b9d1b2abd026532ed68d43e39a12c0db978208231",
    "profiles/fragments/profile_fragment_018.toml": "efdd30a46964b3eb56db14d474311ce4b23cf247f4c20a278f7788853f26d1ee",
    "profiles/fragments/profile_fragment_019.toml": "5ab6bdc584472b442b43375dea039e17267e348fa60aad4832ce108afbae12d7",
    "profiles/fragments/profile_fragment_020.toml": "ba9e6e09a2ea26bb2f53622f9e3d7582e3a6a4a82764d66c26ff5ca57e92db07",
    "profiles/fragments/profile_fragment_021.toml": "996c8089f8a0203086116441514e624b5f865e8aa15a98c872b7b347f519b93a",
    "profiles/fragments/profile_fragment_022.toml": "988f85a7f91293d7042dd0c14690f05e0680e715fe568c6618388bfb3da9087e",
    "profiles/fragments/profile_fragment_023.toml": "5f36a52067a1d8cf09ff1ed6ead211a81269171445b1df4d33e9e0c2329fac05",
    "profiles/fragments/profile_fragment_024.toml": "25af371527e85959a44082f9b6467c4ffd510298d1e1a3d5e3e3bd6d061f1f90",
    "profiles/fragments/profile_fragment_025.toml": "c386eb8c3cb649fadfc23edc1702699ffe42a5fcc96d637ee1cf9653e48d813c",
    "profiles/fragments/profile_fragment_026.toml": "bd101cbb418661dcd2252b7af1855162b00354ac2d36fa600bf344228b37ba6b",
    "profiles/fragments/profile_fragment_027.toml": "d5144b34e53bd6b53a8a520e9f5bff251105f81088ad48f6caf6a6a3b2047e28",
    "profiles/fragments/profile_fragment_028.toml": "f02cd6f5dda52e2b3de9a0de51c48417cbcb8d4aec3eb2ee4ab42a7e6dcd7571",
    "profiles/fragments/profile_fragment_029.toml": "ab43c6114d7ca9898c80f05def63dca3c9d4896e4e04f7d883eda095cfdc6939",
    "profiles/fragments/profile_fragment_030.toml": "c0f8c63de246b02b1f520c2a2763de38345785de27e65519f7a218067427d99b",
    "profiles/fragments/profile_fragment_031.toml": "edfd55f66b92d963d15989b7db55e7ef2003dbd659a6d99cf6ba3f4b4965a9b5",
    "profiles/fragments/profile_fragment_032.toml": "a9cf269bb723d26d26eca9e12ef916c99a28860bc2ec41c85ab9f5fb2cfd7312",
    "profiles/fragments/profile_fragment_033.toml": "72fa2f761fdd31928956a4c5dcc87e82201fec8b2c2e8c70e3c301b06d0c7067",
    "profiles/fragments/profile_fragment_034.toml": "9c54dae503d1b740739f31e9602ae9e8d308f7582c81846599bb1d1231bc4f6a",
    "profiles/fragments/profile_fragment_035.toml": "07e206cf7d504340277d535c756099628afb94c0d7d9f8eb772008b3ba02a871",
    "profiles/fragments/profile_fragment_036.toml": "a02d979b4a6458fa8e64d27bddfea6b788c933b591d626c411c4086a7173d56b",
    "profiles/fragments/profile_fragment_037.toml": "f9bfde0fd3d7e7e7fb88ab396fc83fc4458c782dea1e6d2a475f6b62b1ec7f00",
    "profiles/fragments/profile_fragment_038.toml": "8edea2090fa0332475fbaf942aab6b3d7f9c76771862d2f7216705471dbc2104",
    "profiles/fragments/profile_fragment_039.toml": "0adb69836d6b9b51f55c7dcd904203dd85c17691d3a2ddd8e222c4df88c97b71",
    "profiles/fragments/profile_fragment_040.toml": "142cf69cb89d1a70e252d94ca31d565ab2d4ab68392a283ffeb4045d8ef18b3e",
    "profiles/fragments/profile_fragment_041.toml": "e9bbbc479968204bef6a4a4afeca97092c5eafa40b6eb5fa13ea307af2cd92f9",
    "profiles/fragments/profile_fragment_042.toml": "2bfa3202bccadad067576bac17e666e78798fae68d021fce62607318f31a35fa",
    "profiles/fragments/profile_fragment_043.toml": "1ae208bd139875a44cd53c8d7504662064aefc5cc0bf7bf1f988cc14a0af3278",
    "profiles/fragments/profile_fragment_044.toml": "fa91abb9723882ee90097a3a94d728ecfe3461e0af44a7c4f8b2884abde877c4",
    "profiles/fragments/profile_fragment_045.toml": "156702a019b32e4bb0b37f9a1ff4fef4097c6b59448ab35dffd1806c6c5f7f5e",
    "profiles/fragments/profile_fragment_046.toml": "dac749c71a2824066afeb7e8220c47d0c554dad79ca1b9ff5003a5cb1244eed0",
    "profiles/fragments/profile_fragment_047.toml": "ec17f4a6bd813ccda6f3e424c2eac2e8620c5df21ea6968259d80c6a2e9f8e6a",
    "profiles/fragments/profile_fragment_048.toml": "78c4c78106e53b276a3134d7394a085b6ca399e4b57eaebf44a617bd13ee939c",
    "profiles/fragments/profile_fragment_049.toml": "b0b9ab2f51d0021880cadb770ff276535d1e404d2e2e884f8c6383f497a0183b",
    "profiles/fragments/profile_fragment_050.toml": "c187f2aca723f18d0890d66237abf9515ba656f9068a2d3b094e6e4dc1a710ba",
    "profiles/fragments/profile_fragment_051.toml": "123cf1cf4e85f75325df00db1164a995866bbf5456be4a112a9521a0e55e7705",
    "profiles/fragments/profile_fragment_052.toml": "f6bb4e2015cf8bc40cd9ae0bea772ee9728c50948efe485331c0799081f03298",
    "profiles/fragments/profile_fragment_053.toml": "3a02ffa29bc5ee79436323bf47a205881affbbb13357025c4ed00718c4f0bae9",
    "profiles/fragments/profile_fragment_054.toml": "8d5856c8da813c56c402ce2534af0c553d69a12f9bac65ec5933986705b89469",
    "profiles/fragments/profile_fragment_055.toml": "1fcdc8512dd74bda7274cf6622c0a804ffbfde5ee39a98d528d120e2b38b68aa",
    "profiles/fragments/profile_fragment_056.toml": "fdb24f26a05b13e509d685b90ea690e5ccbe9ae235ca1b792129fe5520446ef5",
    "profiles/fragments/profile_fragment_057.toml": "62b515ef5968d4121ada67ec69d48094105d1bc4ada71ff3331b96dd930af8c4",
    "profiles/fragments/profile_fragment_058.toml": "7799d8b18582c842ec014ce76e6b91845b725e0bbbfc0f0b5e8f8d12b9abb8b0",
    "profiles/fragments/profile_fragment_059.toml": "049c2ef4e857f7e4b9494ca9a981a6e5e843e29e16f07b88907113ab3befc91e",
    "profiles/fragments/profile_fragment_060.toml": "c47bb912b29100d0ddd4ae2a7f65e73110f9f3488489d9cbbc9aec89d0a3d1cb",
    "profiles/fragments/profile_fragment_061.toml": "9581461fc5e8adfc795bbb3dbb6981fc0221aa07e015cfad87143b86f8a980fb",
    "profiles/fragments/profile_fragment_062.toml": "d6e5c63aaa5e957469b826031ad94613e22876a4fd6ab41227854ff19623b040",
    "profiles/fragments/profile_fragment_063.toml": "fb56bc19eadc9434c9a0cd0bfef5c5755c671a85c1b8e6b1ac4c80880bc87387",
    "profiles/fragments/profile_fragment_064.toml": "f320ff6f6f3f18a63447cc427085846c41142595d883ca70928e2104d63e8890",
    "profiles/fragments/profile_fragment_065.toml": "c71f7e0e8e4564d004b97b78b4d530145aeef1748a5801e75766b4c661b82cc8",
    "profiles/fragments/profile_fragment_066.toml": "63a4fb31d0327cc627d2299f33098e43376b326a37ccd61d6e3a7321c92f38b4",
    "profiles/fragments/profile_fragment_067.toml": "d1b0c645f5ee9d4a451acf8a5784f4d5e4746333815f1c7fe056e43d3504a42d",
    "profiles/fragments/profile_fragment_068.toml": "4aa70b63854cdaeba976898f35a9d560783ebeb67b3b7f8b35192e034f044738",
    "profiles/fragments/profile_fragment_069.toml": "a1797512f969c99740eb866884aead84dc3e43f6b1c6480c3c5501407babf9c4",
    "profiles/fragments/profile_fragment_070.toml": "e49d164c5c69c931fd4a81a5f9aa91baae00e9302e082666138543535de62cc7",
    "profiles/fragments/profile_fragment_071.toml": "731a7abcea97f95c08b448f296bc08b3d8146b5477f17570936dc42ae5a509f3",
    "profiles/fragments/profile_fragment_072.toml": "676345e7ff8354536b68e94cebe47e668e58bb84404d0799bc48ae17c664db49",
    "profiles/fragments/profile_fragment_073.toml": "1cc14895d58b3bdc8290488c5e615af8202550fafc8d9dedf7a78c9ee4ad1def",
    "profiles/fragments/profile_fragment_074.toml": "90a32fe13bea3ca5a0fdc4af2223d0f1b2cda3289ae9cce89b2df35bbfafe4a5",
    "profiles/fragments/profile_fragment_075.toml": "4aba31f7dd2b991a8fb16ea759e0c75837b5bbebda27b86e90543cce57f70f6c",
    "profiles/fragments/profile_fragment_076.toml": "a9c2fd881dffed44ec16ca4a8262ed28ef068f641628d47bfada6843606daf40",
    "profiles/fragments/profile_fragment_077.toml": "1d5ca17c45bd7f813161756afae7cc5740e6456823223ba8be4bfee39e7c6de3",
    "profiles/fragments/profile_fragment_078.toml": "ea639577e2111ee7c3ba4c8573568e24b13b1ac097bfbc725c6ee8d207a4ba4a",
    "profiles/fragments/profile_fragment_079.toml": "d4ca844ab2fca9e61908375f79eece63fece5de3e8f397e63c3a2548a6e381c8",
    "profiles/fragments/profile_fragment_080.toml": "ce888f7ea2401e97d3fff2779addc6a78434fb81c4b0d9e6315a95010ac405eb",
    "profiles/fragments/profile_fragment_081.toml": "3dddfc1ff349ddce0b5e7c74b1b3bf664d3ba735afd5fabf68a0817e01e52bb6",
    "profiles/fragments/profile_fragment_082.toml": "1485c8f722d426996a85f8d40708234d9e66c1fe5b3bdb801b04d07c3647b8e9",
    "profiles/fragments/profile_fragment_083.toml": "21e428f4f09273daea11678cb6319582b148e44bf19f0f9c9dc39fae12b8a902",
    "profiles/fragments/profile_fragment_084.toml": "b89531b06b064c58e22daae2cf344a80d8f705ef8a72c35571889bba492a3ec1",
    "profiles/fragments/profile_fragment_085.toml": "9d8a0c807ee5f033630c67837a1dba9b73d814b231261c956ba2ed5e41780561",
    "profiles/fragments/profile_fragment_086.toml": "0c65a2379616b38c561f2f544e3b1e8eefe0fe19d4f275a18645c2883e220918",
    "profiles/fragments/profile_fragment_087.toml": "9afa5882a5169496098f93fffde90fce874eb6e6caf4f44433fbeb3c6707b1e7",
    "profiles/fragments/profile_fragment_088.toml": "2189f908cfa47a9764a73e7cf340b35054406eb732130c2c8416e5669ef400d5",
    "profiles/fragments/profile_fragment_089.toml": "6e4f5d1755aede5ef7eafe8f7a16a74232d6339808144bea4883a9b1244fe0d0",
    "profiles/fragments/profile_fragment_090.toml": "ef93450478642ffefa0db1ae68675016eaf27b633ba31009f788bb532570e278",
    "profiles/fragments/profile_fragment_091.toml": "38ee11a3e2e09494926a3bc758c1d11129564afe20c6149a59e782e9b3e2b6bc",
    "profiles/fragments/profile_fragment_092.toml": "6224731a6d8a4525b6bd19ec98751ebcee80625af536f188888d54f3b2612a0a",
    "profiles/fragments/profile_fragment_093.toml": "eb81fc277020de3d2a9a6c715f5ae1e8ff1c57e1f29f057060e24caed9e7a38b",
    "profiles/fragments/profile_fragment_094.toml": "b058eb4b71baf33f7a5989b54a7956643a8b31d2e02424065e07474f77dea2ef",
    "profiles/fragments/profile_fragment_095.toml": "698699d04d0bebd0a84509c2d5028588a6a9cc729fcb40939bd6bcc16a04bc3a",
    "profiles/fragments/profile_fragment_096.toml": "f079cca9415b3d47d7d8dd61d58d47e03073c4f92160034db3d40a527ad29174",
    "profiles/fragments/profile_fragment_097.toml": "367e209d208e5e080ecfd0b313b3a19ac9a93c233d69bc5c4ffec635de3c0338",
    "profiles/fragments/profile_fragment_098.toml": "fe9b046725460f039663c631ffea53f489bb38be484549b95f4a1ce13c03ff92",
    "profiles/fragments/profile_fragment_099.toml": "01206725f7b2983c1e03d744e07b08d7396a62f6299b35ee9c5bf55a894181fe",
    "profiles/fragments/profile_fragment_100.toml": "0c942fd10babb8c914dcf8213907783f63889087ddb5d46e0cbdf412f3fe5bfc",
    "profiles/fragments/profile_fragment_101.toml": "698a7f8827717a08623154506b3e6e1a08ee4d193ba430ea38c113fc4ec78ecc",
    "profiles/fragments/profile_fragment_102.toml": "5648b50aae12b2fb65fd07c4a960ed6cb765bcccb0c14015228a05b26834cdee",
    "profiles/fragments/profile_fragment_103.toml": "cde34402918c5548a8ef1c4bffcf31840ff43f40ebdfc4c8842208a06e3e0ba3",
    "profiles/fragments/profile_fragment_104.toml": "3c42bdea06c1cc14ccaf5a2e80a733339b6b5886122c6bad1be6768c033a5eae",
    "profiles/fragments/profile_fragment_105.toml": "dedd07218975f67681e2bd9e83f1df159f48871188fbf65471fdc8e5a6e4e9b7",
    "profiles/fragments/profile_fragment_106.toml": "1a997ba21f2d50df6da1d134fc6f248f1b6237835d090300326a955128623c57",
    "profiles/fragments/profile_fragment_107.toml": "23ae02880fe4b22bf8f7babe526aad5db924c2be74b60606b1c9b98c0e0acd9d",
    "profiles/fragments/profile_fragment_108.toml": "b61c14960fc4b6028d76657d2611b7a5fb13c24095e9c1c3f669829f6c5dd887",
    "profiles/fragments/profile_fragment_109.toml": "88d47c23fadd4f666324ce34ef70627ed9cfee9c8d15a1e4f6a23ff5ed9df680",
    "profiles/fragments/profile_fragment_110.toml": "e0af85cf9bd9d176323be89dff3c81cde3f1813920282071aa9054a3206b04a3",
    "profiles/fragments/profile_fragment_111.toml": "42b6aca4e1f8dfcebc5fd0d0f0b1a83a1616c93c074a6fae703b8f183b8f7a27",
    "profiles/fragments/profile_fragment_112.toml": "f6e3868658c23c384055f97a43c13a96d38218796ef1561e8acd2679a28adfba",
    "profiles/fragments/profile_fragment_113.toml": "cd074e41a4184fcb8acd9acf215a1dda1bbb01cfd02aad10ebf5fca552e66927",
    "profiles/fragments/profile_fragment_114.toml": "aa3d93783fd604cc9ed3de50032b6a2022aa00e60dfa73e5c66c435371c2bf1f",
    "profiles/fragments/profile_fragment_115.toml": "d35d38e7010053c0e62d9d7eb506430d5a8ecad06e78dc81198522d57191ed76",
    "profiles/fragments/profile_fragment_116.toml": "5657c13e1051043660a5e23b8ebdd7a13ea0a8ed5d181ac7f2b82c979f1fdb84",
    "profiles/fragments/profile_fragment_117.toml": "1de4bac350aac78032c8ea8bb39e49508aafb35949b9f985c2be854cb598adb3",
    "profiles/fragments/profile_fragment_118.toml": "3c912842477bbc421c4f59dc3e1ab008cb4d07dfad22500d7f31d842a3f28116",
    "profiles/fragments/profile_fragment_119.toml": "b9e669b5fb67c336f5fcda2bd83511d1e84a1fef6bb1102e7625b425241279e5",
    "profiles/fragments/profile_fragment_120.toml": "e0cb320576675786e40a33aebeb33c4ca337c1a8958f222e3766f5dd350c1880",
}


def test_protected_inputs_are_unchanged():
    """Verify contract inputs were not modified to bypass the security-audit checks."""
    for rel, expected in EXPECTED_HASHES.items():
        p = APP / rel
        assert p.exists(), f"missing protected file {p}"
        assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, (
            f"protected file changed: {rel}"
        )


def test_visible_case_matches_reference(tmp_path):
    """Build the C++ CLI and compare the visible case artifacts to the reference replay model."""
    build_binary()
    state = APP / "state/ledger.tsv"
    if state.exists():
        state.unlink()
    out = tmp_path / "visible-out"
    run_case(APP / "cases/visible", out)
    assert read_outputs(out) == reference(APP / "cases/visible")


def test_replay_is_idempotent_and_byte_stable(tmp_path):
    """Running the same case twice must not duplicate ledger effects or change output bytes."""
    build_binary()
    state = APP / "state/ledger.tsv"
    if state.exists():
        state.unlink()
    out = tmp_path / "stable-out"
    run_case(APP / "cases/visible", out)
    first = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    ledger1 = state.read_text().splitlines()
    run_case(APP / "cases/visible", out)
    second = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    ledger2 = state.read_text().splitlines()
    assert first == second
    assert ledger1 == ledger2
    assert len(ledger2) == len(set(ledger2))
    for line in ledger2:
        fields = line.split("\t")
        assert len(fields) == 5
        assert fields[0] == "visible-fleet-042"
        assert fields[1].startswith("CARD-")
        assert fields[2] in {"TXN", "SCRIPT"}
        assert len(fields[3]) == 4
        int(fields[3], 16)
        assert fields[4]
    assert_ledger_matches(APP / "cases/visible")


def make_hidden_case(base: Path):
    """Create a shuffled fraud-audit case with continuations and duplicates."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.meta").write_text(
        "case_id=hidden-fleet-901\nprofile=fleet\ncreated_at=2026-06-03T00:00:00Z\n"
    )
    lines = [
        "record|batch|session|card|terminal|atc|kind|pan_token|amount|currency|cid|host|sw|tlv|script_id|seq",
        "H004|B7|S-H2|CARD-H|TERM-2|0A02|SCRIPT|tok-h|900|USD|||6A80|7112BF0C0FDF2202BEEF8608AAAABBBBCCCCDDDD|SCR-H2|4",
        "H001|B7|S-H1|CARD-H|TERM-1|0A00|TXN|tok-h|700|USD|online_approved|approved|9000|6F0A8408A0000000031010||1",
        "H006|B8|S-H3|CARD-Z|TERM-9|0001|TXN|tok-z|1900|GBP|online_approved|timeout|6104|6F068404A0000004||1",
        "H002|B7|S-H2|CARD-H|TERM-2|0A02|TXN|tok-h|900|USD|offline_approved|declined|9000|7016BF0C13DF2002CAFE9F36020A02DF2101EE||2",
        "H003|B7|S-H2|CARD-H|TERM-2|0A02|SCRIPT|tok-h|900|USD|||6300|710EDF2101EE8602CAFE|SCR-H1|3",
        "H007|B8|S-H3|CARD-Z|TERM-9|0001|TXN|tok-z|1900|GBP|online_approved|timeout|9000|7010DF3002ABCD9F2701805F2A020826||2",
        "H005|B7|S-H2|CARD-H|TERM-2|0A02|TXN|tok-h|900|USD|offline_approved|declined|9000|7016BF0C13DF2002CAFE9F36020A02DF2101EE||5",
        "H008|B7|S-H2|CARD-H|TERM-2|0A02|SCRIPT|tok-h|900|USD|||6300|710EDF2101EE8602CAFE|SCR-H1|6",
        'H009|B7|S-H1|CARD-H|TERM-1|0A00|SCRIPT|tok-h|700|USD|||9100|710EDF3102CAFE8602BEEF|SCR-"Q\\B|7',
    ]
    (base / "trace.tsv").write_text("\n".join(lines) + "\n")


def make_empty_artifact_case(base: Path):
    """Create a valid case with no issuer-script or reversal rows."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.meta").write_text(
        "case_id=empty-artifacts-017\nprofile=fleet\ncreated_at=2026-06-04T00:00:00Z\n"
    )
    lines = [
        "record|batch|session|card|terminal|atc|kind|pan_token|amount|currency|cid|host|sw|tlv|script_id|seq",
        "E001|B9|S-E1|CARD-E|TERM-E|0007|TXN|tok-e|500|USD|online_approved|approved|9000|700ADF4002CAFE5F2A020840||1",
    ]
    (base / "trace.tsv").write_text("\n".join(lines) + "\n")


def make_default_profile_case(base: Path):
    """Create a case that exercises default profile fallback controls."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.meta").write_text(
        "case_id=default-profile-031\n"
        "profile=missing-default\n"
        "created_at=2026-06-05T00:00:00Z\n"
    )
    lines = [
        "record|batch|session|card|terminal|atc|kind|pan_token|amount|currency|cid|host|sw|tlv|script_id|seq",
        "D001|B5|S-D1|CARD-D|TERM-D|0003|TXN|tok-d|1100|USD|online_approved|approved|9000|700ADF5002CAFE5F2A020840||1",
        "D002|B5|S-D1|CARD-D|TERM-D|0003|SCRIPT|tok-d|1100|USD|||6300|710ADF5102BABE8602CAFE|SCR-D|2",
    ]
    (base / "trace.tsv").write_text("\n".join(lines) + "\n")


def make_numeric_seq_case(base: Path):
    """Create a case where decimal sequence order differs from string order."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.meta").write_text(
        "case_id=numeric-seq-210\nprofile=fleet\ncreated_at=2026-06-07T00:00:00Z\n"
    )
    lines = [
        "record|batch|session|card|terminal|atc|kind|pan_token|amount|currency|cid|host|sw|tlv|script_id|seq",
        "N001|BN|S-N1|CARD-N|TERM-N|0002|TXN|tok-n|600|USD|online_approved|timeout|6104|6F068404A000||2",
        "N002|BN|S-N1|CARD-N|TERM-N|0002|TXN|tok-n|600|USD|online_approved|timeout|9000|700ADF8002CAFE||10",
        "N003|BN|S-N1|CARD-N|TERM-N|0002|SCRIPT|tok-n|600|USD|||6300|710ADF8102BEEF|SCR-N|11",
    ]
    (base / "trace.tsv").write_text("\n".join(lines) + "\n")


def test_hidden_mutated_case_matches_reference(tmp_path):
    """A shuffled hidden case checks continuation joining, nested private tags, duplicates, and reversal precedence together."""
    build_binary()
    state = APP / "state/ledger.tsv"
    if state.exists():
        state.unlink()
    case = tmp_path / "hidden-case"
    make_hidden_case(case)
    out = tmp_path / "hidden-out"
    run_case(case, out)
    summary, scripts, reversals, digest = read_outputs(out)
    assert (summary, scripts, reversals, digest) == reference(case)
    assert_ledger_matches(case)
    assert summary["totals"]["scripts_seen"] > summary["totals"]["scripts_kept"]
    assert any(
        row["script_id"] == 'SCR-"Q\\B' and row["status"] == "applied"
        for row in scripts
    )


def test_trace_sequence_values_sort_numerically(tmp_path):
    """Trace sequence values such as 2 and 10 must use numeric ordering."""
    build_binary()
    state = APP / "state/ledger.tsv"
    if state.exists():
        state.unlink()
    case = tmp_path / "numeric-seq-case"
    make_numeric_seq_case(case)
    out = tmp_path / "numeric-seq-out"
    run_case(case, out)
    summary, scripts, reversals, digest = read_outputs(out)
    assert (summary, scripts, reversals, digest) == reference(case)
    assert_ledger_matches(case)
    assert summary["sessions"][0]["first_atc"] == "0002"
    assert any(row["status"] == "failed_soft" for row in scripts)
    assert [row[4] for row in reversals] == ["host_timeout_after_online_approval"]


def test_output_contract_details(tmp_path):
    """Verify exact file set, key order, newline policy, empty artifacts, and digest formatting."""
    build_binary()
    out = tmp_path / "contract-out"
    run_case(APP / "cases/regression", out)
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == [
        "audit_digest.txt",
        "case_summary.json",
        "issuer_scripts.jsonl",
        "reversal_queue.csv",
    ]
    for name in ["case_summary.json", "reversal_queue.csv", "audit_digest.txt"]:
        data = (out / name).read_bytes()
        assert data.endswith(b"\n"), f"{name} must end with newline"
    digest = (out / "audit_digest.txt").read_text()
    assert digest.startswith("cardrail-v1 ")
    assert len(digest.strip().split()[1]) == 16
    summary = json.loads((out / "case_summary.json").read_text())
    assert set(summary) == {
        "schema_version",
        "case_id",
        "profile_id",
        "totals",
        "sessions",
    }
    assert summary["profile_id"] == "fleet-issuer-override"

    summary_pairs = json_pairs(out / "case_summary.json")
    assert pair_keys(summary_pairs) == [
        "schema_version",
        "case_id",
        "profile_id",
        "totals",
        "sessions",
    ]
    assert pair_keys(pair_value(summary_pairs, "totals")) == [
        "transactions_seen",
        "transactions_kept",
        "scripts_seen",
        "scripts_kept",
        "scripts_applied",
        "scripts_failed_soft",
        "scripts_failed_hard",
        "reversals_queued",
    ]
    first_session = pair_value(summary_pairs, "sessions")[0]
    assert pair_keys(first_session) == [
        "session_id",
        "card_id",
        "lineage_key",
        "terminal_ids",
        "first_atc",
        "last_atc",
        "approved",
        "declined",
        "scripts_applied",
        "scripts_failed_soft",
        "scripts_failed_hard",
        "reversal_required",
        "private_tags",
    ]
    first_script = json.loads(
        (out / "issuer_scripts.jsonl").read_text().splitlines()[0],
        object_pairs_hook=lambda pairs: pairs,
    )
    assert pair_keys(first_script) == [
        "case_id",
        "session_id",
        "card_id",
        "lineage_key",
        "script_id",
        "atc",
        "status",
        "terminal_id",
        "private_tags",
    ]

    empty_case = tmp_path / "empty-case"
    empty_out = tmp_path / "empty-out"
    make_empty_artifact_case(empty_case)
    run_case(empty_case, empty_out)
    assert (empty_out / "issuer_scripts.jsonl").read_bytes() == b""
    assert (empty_out / "reversal_queue.csv").read_text() == (
        "case_id,session_id,card_id,atc,reason,amount,currency\n"
    )
    assert read_outputs(empty_out) == reference(empty_case)


def test_manifest_profile_fallback_without_etc_override(tmp_path):
    """Removing the /etc override should make the manifest-selected profile win."""
    build_binary()
    override = Path("/etc/cardrail/profile.toml")
    saved = override.read_bytes() if override.exists() else None
    if override.exists():
        override.unlink()
    try:
        case = tmp_path / "fallback-case"
        make_empty_artifact_case(case)
        out = tmp_path / "fallback-out"
        run_case(case, out)
        assert (
            json.loads((out / "case_summary.json").read_text())["profile_id"] == "fleet"
        )
        assert read_outputs(out) == reference(case)
    finally:
        if saved is not None:
            override.write_bytes(saved)


def make_security_audit_case(base: Path):
    """Create a threat-model case with replayed ATCs and reversal evidence."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.meta").write_text(
        "case_id=security-controls-221\nprofile=fleet\ncreated_at=2026-06-06T00:00:00Z\n"
    )
    lines = [
        "record|batch|session|card|terminal|atc|kind|pan_token|amount|currency|cid|host|sw|tlv|script_id|seq",
        "S004|BA|S-A2|CARD-A|TERM-B|0102|SCRIPT|tok-a|1200|USD|||6A80|7110DF6202BEEF8604DEADBEEF|SCR-A2|4",
        "S001|BA|S-A1|CARD-A|TERM-A|0100|TXN|tok-a|1200|USD|online_approved|timeout|6108|6F088406A00000033301||1",
        "S002|BA|S-A1|CARD-A|TERM-A|0100|TXN|tok-a|1200|USD|online_approved|timeout|9000|7010BF0C0DDF6002CAFE9F36020100||2",
        "S003|BA|S-A2|CARD-A|TERM-B|0102|TXN|tok-a|1200|USD|offline_approved|declined|9000|7010DF6102ABCD9F36020102||3",
        "S005|BA|S-A2|CARD-A|TERM-B|0102|SCRIPT|tok-a|1200|USD|||6A80|7110DF6202BEEF8604DEADBEEF|SCR-A2|5",
        "S006|BA|S-A3|CARD-A|TERM-C|0102|TXN|tok-a|1200|USD|offline_approved|declined|9000|7010DF6102ABCD9F36020102||6",
        "S007|BB|S-B1|CARD-B|TERM-Z|0001|TXN|tok-b|400|EUR|offline_approved|approved|9000|700ADF7002FACE5F2A020978||1",
        "S008|BB|S-B1|CARD-B|TERM-Y|0001|TXN|tok-b|400|EUR|offline_approved|approved|9000|700ADF7002FACE5F2A020978||2",
    ]
    (base / "trace.tsv").write_text("\n".join(lines) + "\n")


def test_security_controls_emit_replay_reversal_and_digest_evidence(tmp_path):
    """Threat-model controls should survive duplicates, continuations, and reruns."""
    build_binary()
    state = APP / "state/ledger.tsv"
    if state.exists():
        state.unlink()
    case = tmp_path / "security-case"
    make_security_audit_case(case)
    out = tmp_path / "security-out"

    run_case(case, out)
    first = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    summary, scripts, reversals, digest = read_outputs(out)
    assert (summary, scripts, reversals, digest) == reference(case)

    reasons = {(row[2], row[3], row[4]) for row in reversals}
    assert ("CARD-A", "0100", "host_timeout_after_online_approval") in reasons
    assert ("CARD-A", "0102", "issuer_script_hard_failure") in reasons
    assert ("CARD-B", "0001", "duplicate_atc_replay_attempt") in reasons
    session_a2 = next(s for s in summary["sessions"] if s["session_id"] == "S-A2")
    assert session_a2["private_tags"] == ["DF61", "DF62"]
    assert session_a2["scripts_failed_hard"] == 1
    assert sum(1 for row in scripts if row["script_id"] == "SCR-A2") == 1
    assert digest.startswith("cardrail-v1 ") and digest.endswith("\n")

    run_case(case, out)
    second = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    assert first == second
    assert_ledger_matches(case)


def test_default_profile_fallback_controls_status_and_private_tags(tmp_path):
    """Default fallback disables private tags and treats unlisted script warnings as hard failures."""
    build_binary()
    override = Path("/etc/cardrail/profile.toml")
    saved = override.read_bytes() if override.exists() else None
    if override.exists():
        override.unlink()
    try:
        case = tmp_path / "default-profile-case"
        make_default_profile_case(case)
        out = tmp_path / "default-profile-out"
        run_case(case, out)
        summary, scripts, _reversals, _digest = read_outputs(out)
        assert summary["profile_id"] == "default"
        assert summary["sessions"][0]["private_tags"] == []
        assert scripts[0]["private_tags"] == []
        assert scripts[0]["status"] == "failed_hard"
        assert read_outputs(out) == reference(case)
    finally:
        if saved is not None:
            override.write_bytes(saved)
