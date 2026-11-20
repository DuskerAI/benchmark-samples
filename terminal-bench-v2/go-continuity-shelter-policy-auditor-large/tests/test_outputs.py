import csv
import json
import math
import shutil
import subprocess
from pathlib import Path

APP = Path("/app/task_file")
POLICY_ROOT = Path("/opt/continuity-policy-corpus")
CASE_ROOT = Path("/var/lib/continuity-audit/cases")
CONFIG = Path("/etc/continuity-audit/config.json")
OUT = Path("/var/lib/continuity-audit/output")
BIN = APP / "bin" / "continuity-audit"
EXPECTED_HASHES = {
    "cases/delta-large-009.json": "d1e17755b439bde7b6dba5d8e059e801a51afa9a8daf780a94c1c0600c7a14cf",
    "cases/delta-overflow-004.json": "cc67607615f4cd6b5fffe366e1c10328ee1364a92150a269d3fa91051243db1d",
    "cases/harbor-001.json": "cb08cb62f7fe6839021502a872e900dc4e83aead220ee197e1aed23eb3020cef",
    "cases/harbor-med-008.json": "f3c3a43bb7fbf4c91c9e32f8fb94f1b33e1cbaa381ee8950be1ecd008f5895da",
    "cases/harbor-stale-013.json": "c151aa81210667b10682619e67dff9eb34deb363e93328de44558272e81f9d33",
    "cases/inland-pets-005.json": "f159666214d21200063e58e5ea4b4768f493d7598f6ca2bcd6f601c6612be1f3",
    "cases/inland-waiver-010.json": "d576776143af5866f5d54de2149c99842375391607b5eff95519c689fe5f1405",
    "cases/island-clean-012.json": "2eaf7c34a73169dea12b6ee2ddde63ee88c4fa1328269b2def0608786b649669",
    "cases/island-med-003.json": "d87c8ccc9a227f3f241ad57cae78b8127d11e1d82b9166467bd3164d1e3471de",
    "cases/island-med-007.json": "689515b9115e5f5aab75f8593848fa40aeacb1469a5cecbaeecb647c8192b664",
    "cases/ridge-evac-002.json": "da1c7b4f0e289436e2addd5d40eb7883cbaba05485f70d886cb7b360b9437d44",
    "cases/ridge-evac-006.json": "2a505717ec861bb8e1576781b7b307e51242389e2e2a1d7a12aecd84c1847ad7",
    "cases/ridge-transport-011.json": "817653a714e5deb2b21608afbc84fbb1c0fc1ffc516ef8b072708e940f835804",
    "config/config.json": "4dbb981b758997bd39dcc44ef5dd1a93e4082d4bcb1cf88254e7d5b2be88ec0b",
    "policy-corpus/amendments/2025-11-medical-power.md": "4509860101f3a5df947140af1bb3ddb31d3b8637313db0bc4f08d723d1a542b6",
    "policy-corpus/amendments/2025-12-staffing.md": "4a84804b423270d0a27f7c8e2b3b0c5ddf4e6eaf415d3621ba630fa1e600ce5a",
    "policy-corpus/amendments/2026-02-island-water.md": "00a5333d3acfa934a0f2a11f8748b6713688bda31dae387c404629f3256d5398",
    "policy-corpus/annexes/delta-accessibility.md": "a0a400fabfca0bc91b507c2c977abd31b453625c449f89c134b5c134021b3c9d",
    "policy-corpus/annexes/transport-window.md": "01458001d7853b97eec8ac007a35d8f87c20381518821966338acecccd0bf8c2",
    "policy-corpus/bulletins/generator-recency.md": "6c4be2e1268ee28469f8f326595e428ef611e1d47db157030eb1f788f04f1caa",
    "policy-corpus/errata/2026-01-ridge-generator.md": "589afe3519172f4593ee1a15f4828a7bb7d540e4d443303e134604dbdf0189bf",
    "policy-corpus/handbook/operations-01.md": "deec6bf55654b5b29723024a051acf67cc0648c01933c55ee5d373f326ed4b93",
    "policy-corpus/handbook/operations-03.md": "c921b7565a4c3b3aee8e237ca97fb64a9425eb5823d28a1ddff845891062edc6",
    "policy-corpus/handbook/operations-07.md": "3bd20da2455da37327d39c0df1aee155067a7e9dcf2c4aed17b24d7c77509493",
    "policy-corpus/index.md": "5047613cc1faafd621560ef5bc02ba1b104a7c7f92c5ded71ba8acb63c3960c1",
    "policy-corpus/memos/harbor-advisory-pets.md": "3f5fb3de635a85b1ab9dd489170b3971253faa1420c231f2f436f9fb42bd90e5"
}

RULES = [
    {"id":"ACCESS_DELTA_LARGE","value":"delta shelters over 80 residents require accessibility retrofit","source":"annexes/delta-accessibility.md","priority":70},
    {"id":"GEN_BASE","value":"minimum generator class C","source":"handbook/operations-01.md","priority":10},
    {"id":"GEN_MEDICAL","value":"medical dependency requires generator class B","source":"amendments/2025-11-medical-power.md","priority":50},
    {"id":"GEN_RECENCY","value":"generator test must be within seven days before opening","source":"bulletins/generator-recency.md","priority":72},
    {"id":"GEN_RIDGE_MANDATORY","value":"ridge mandatory evacuation requires generator class A","source":"errata/2026-01-ridge-generator.md","priority":90},
    {"id":"PET_HARBOR_ADVISORY","value":"harbor advisory cases may colocate pets without a pet wing","source":"memos/harbor-advisory-pets.md","priority":80},
    {"id":"PET_STANDARD","value":"pet cases require pet accommodation","source":"handbook/operations-07.md","priority":10},
    {"id":"STAFFING","value":"default 32 residents per staff, 26 medical, 24 mandatory","source":"amendments/2025-12-staffing.md","priority":60},
    {"id":"TRANSPORT_MANDATORY","value":"mandatory evacuation requires confirmed transport unless narrow pending exception applies","source":"annexes/transport-window.md","priority":65},
    {"id":"WATER_DEFAULT","value":"minimum three days water reserve","source":"handbook/operations-03.md","priority":10},
    {"id":"WATER_ISLAND","value":"island jurisdiction requires five days water reserve","source":"amendments/2026-02-island-water.md","priority":75},
]

def sha256(path):
    import hashlib
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()

def build_and_run(case_root=CASE_ROOT, out_dir=OUT):
    subprocess.run(["go", "build", "-o", str(BIN), "/app/task_file/cmd/continuity-audit"], cwd=APP, check=True)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(BIN), "--policy-root", str(POLICY_ROOT), "--case-root", str(case_root), "--config", str(CONFIG), "--out-dir", str(out_dir)], cwd=APP, check=True)
    return out_dir

def load_cases(root):
    return [json.loads(p.read_text()) for p in sorted(Path(root).glob("*.json"))]

def gen_rank(v):
    return {"A":3,"B":2,"C":1}.get(v,0)

def active(opened, expires):
    return bool(expires) and opened <= expires

def generator_stale(opened, tested):
    from datetime import date
    opened_d = date.fromisoformat(opened)
    tested_d = date.fromisoformat(tested)
    return tested_d > opened_d or (opened_d - tested_d).days > 7

def evaluate_case(c):
    reasons = []
    applied = {"GEN_BASE", "GEN_RECENCY", "WATER_DEFAULT", "STAFFING", "PET_STANDARD"}
    min_gen = 1
    if c["medical_dependent"]:
        min_gen = max(min_gen, 2)
        applied.add("GEN_MEDICAL")
    if c["jurisdiction"] == "ridge" and c["evacuation_status"] == "mandatory":
        min_gen = max(min_gen, 3)
        applied.add("GEN_RIDGE_MANDATORY")
    if gen_rank(c["generator_class"]) < min_gen:
        reasons.append("GENERATOR_CLASS_TOO_LOW")
    if generator_stale(c["opened_at"], c["generator_tested_at"]):
        reasons.append("GENERATOR_TEST_STALE")
    water_min = 5 if c["jurisdiction"] == "island" and c["opened_at"] >= "2026-02-01" else 3
    if water_min == 5:
        applied.add("WATER_ISLAND")
    if c["water_days"] < water_min:
        reasons.append("WATER_RESERVE_TOO_LOW")
    max_ratio = 32.0
    if c["medical_dependent"]:
        max_ratio = min(max_ratio, 26.0)
    if c["evacuation_status"] == "mandatory":
        max_ratio = min(max_ratio, 24.0)
    ratio = math.inf if c["staff_count"] <= 0 else c["residents"] / c["staff_count"]
    if ratio > max_ratio:
        reasons.append("STAFFING_RATIO_TOO_HIGH")
    if c["pet_count"] > 0 and not c["pet_accommodation"]:
        if c["jurisdiction"] == "harbor" and c["evacuation_status"] == "advisory":
            applied.add("PET_HARBOR_ADVISORY")
        else:
            reasons.append("PET_ACCOMMODATION_MISSING")
    if c["evacuation_status"] == "mandatory":
        applied.add("TRANSPORT_MANDATORY")
        pending_ok = c["transport_status"] == "pending" and gen_rank(c["generator_class"]) >= 3 and c["water_days"] >= 5 and not c["medical_dependent"]
        if c["transport_status"] != "confirmed" and not pending_ok:
            reasons.append("TRANSPORT_NOT_CONFIRMED")
    if c["medical_dependent"] and not c["accessibility_retrofit"]:
        reasons.append("ACCESSIBILITY_RETROFIT_REQUIRED")
    if c["jurisdiction"] == "delta" and c["residents"] > 80:
        applied.add("ACCESS_DELTA_LARGE")
        if not c["accessibility_retrofit"]:
            reasons.append("ACCESSIBILITY_RETROFIT_REQUIRED")
    accepted = set()
    rows = []
    for e in sorted(c.get("exceptions", []), key=lambda x: x["code"]):
        ok = False
        if active(c["opened_at"], e.get("expires_on", "")):
            if e["code"] == "WATER_SHORTAGE_1D" and "WATER_RESERVE_TOO_LOW" in reasons and c["water_days"] >= water_min - 1:
                ok = True
                accepted.add("WATER_RESERVE_TOO_LOW")
            if e["code"] == "STAFFING_10_PERCENT" and "STAFFING_RATIO_TOO_HIGH" in reasons and ratio <= max_ratio * 1.10:
                ok = True
                accepted.add("STAFFING_RATIO_TOO_HIGH")
        rows.append({"case_id": c["case_id"], "exception_code": e["code"], "source": e["source"], "expires_on": e["expires_on"], "accepted": "true" if ok else "false"})
    unresolved = [r for r in reasons if r not in accepted]
    decision = "approve" if not reasons else ("conditional" if not unresolved else "deny")
    return {"case_id": c["case_id"], "jurisdiction": c["jurisdiction"], "decision": decision, "reasons": sorted(reasons), "applied_rules": sorted(applied)}, rows

def expected_for(case_root):
    decisions = []
    exceptions = []
    for c in load_cases(case_root):
        d, rows = evaluate_case(c)
        decisions.append(d)
        exceptions.extend(rows)
    decisions.sort(key=lambda x: x["case_id"])
    exceptions.sort(key=lambda x: (x["case_id"], x["exception_code"]))
    doc_count = sum(1 for p in POLICY_ROOT.rglob("*.md"))
    return {"schema_version":1, "policy_documents":doc_count, "rules": sorted(RULES, key=lambda x: x["id"])}, {"schema_version":1, "case_count":len(decisions), "decisions":decisions}, exceptions

def read_outputs(out_dir):
    eff = json.loads((out_dir / "effective_rules.json").read_text())
    dec = json.loads((out_dir / "case_decisions.json").read_text())
    with (out_dir / "exceptions.csv").open(newline="") as f:
        exc = list(csv.DictReader(f))
    return eff, dec, exc

def test_input_corpus_and_cases_were_not_modified():
    """Verify frozen policy, config, and case inputs still match the shipped hashes."""
    for rel, expected in EXPECTED_HASHES.items():
        path = Path("/") / rel if rel.startswith("opt/") else None
        if rel.startswith("policy-corpus/"):
            path = POLICY_ROOT / rel.removeprefix("policy-corpus/")
        elif rel.startswith("cases/"):
            path = CASE_ROOT / rel.removeprefix("cases/")
        elif rel == "config/config.json":
            path = CONFIG
        assert path is not None and path.exists(), f"missing frozen input {rel}"
        assert sha256(path) == expected, f"frozen input changed: {rel}"

def test_cli_outputs_match_reference_for_bundled_cases():
    """Build the Go CLI, run it on bundled cases, and compare all outputs to the reference semantics."""
    out_dir = build_and_run()
    got = read_outputs(out_dir)
    exp = expected_for(CASE_ROOT)
    assert got == exp

def test_outputs_are_byte_stable_across_repeated_runs():
    """Run the repaired CLI twice and require byte-identical deterministic artifacts."""
    out_dir = build_and_run()
    first = {p.name: p.read_bytes() for p in sorted(out_dir.iterdir())}
    out_dir = build_and_run()
    second = {p.name: p.read_bytes() for p in sorted(out_dir.iterdir())}
    assert first == second

def test_same_schema_added_case_is_evaluated_data_driven(tmp_path):
    """Add an unseen ridge mandatory case and verify data-driven transport/generator denial."""
    work = tmp_path / "cases"
    shutil.copytree(CASE_ROOT, work)
    new_case = {
        "case_id":"hidden-like-900", "jurisdiction":"ridge", "opened_at":"2026-02-15", "shelter_type":"arena",
        "residents":48, "staff_count":2, "medical_dependent":False, "pet_count":1, "pet_accommodation":False,
        "evacuation_status":"mandatory", "generator_class":"B", "generator_tested_at":"2026-02-10",
        "transport_status":"pending", "water_days":4, "accessibility_retrofit":True,
        "exceptions":[{"code":"WATER_SHORTAGE_1D","source":"late-review", "expires_on":"2026-02-28"}]
    }
    (work / "hidden-like-900.json").write_text(json.dumps(new_case, indent=2, sort_keys=True) + "\n")
    out = tmp_path / "out"
    got = read_outputs(build_and_run(work, out))
    exp = expected_for(work)
    assert got == exp
    hidden = [d for d in got[1]["decisions"] if d["case_id"] == "hidden-like-900"][0]
    assert hidden["decision"] == "deny"
    assert "GENERATOR_CLASS_TOO_LOW" in hidden["reasons"]
    assert "TRANSPORT_NOT_CONFIRMED" in hidden["reasons"]

def test_generated_pet_rule_matrix_stays_narrow_and_data_driven(tmp_path):
    """Generated harbor cases verify PET_HARBOR_ADVISORY only applies to pet colocation."""
    work = tmp_path / "cases"
    shutil.copytree(CASE_ROOT, work)
    generated = [
        {
            "case_id":"pet-matrix-901-no-pets", "jurisdiction":"harbor", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":31, "staff_count":1, "medical_dependent":False, "pet_count":0, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-18",
            "transport_status":"pending", "water_days":3, "accessibility_retrofit":True, "exceptions":[]
        },
        {
            "case_id":"pet-matrix-902-colocated", "jurisdiction":"harbor", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":32, "staff_count":1, "medical_dependent":False, "pet_count":3, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-18",
            "transport_status":"pending", "water_days":3, "accessibility_retrofit":True, "exceptions":[]
        },
        {
            "case_id":"pet-matrix-903-pet-wing", "jurisdiction":"harbor", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":62, "staff_count":2, "medical_dependent":False, "pet_count":4, "pet_accommodation":True,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-18",
            "transport_status":"pending", "water_days":3, "accessibility_retrofit":True, "exceptions":[]
        },
        {
            "case_id":"pet-matrix-904-mandatory", "jurisdiction":"harbor", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":72, "staff_count":3, "medical_dependent":False, "pet_count":2, "pet_accommodation":False,
            "evacuation_status":"mandatory", "generator_class":"A", "generator_tested_at":"2026-02-18",
            "transport_status":"confirmed", "water_days":5, "accessibility_retrofit":True, "exceptions":[]
        },
        {
            "case_id":"pet-matrix-905-staff-waiver", "jurisdiction":"harbor", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":105, "staff_count":3, "medical_dependent":False, "pet_count":5, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-18",
            "transport_status":"pending", "water_days":3, "accessibility_retrofit":True,
            "exceptions":[{"code":"STAFFING_10_PERCENT","source":"generated-staff-review", "expires_on":"2026-02-28"}]
        },
        {
            "case_id":"pet-matrix-906-med-no-pets", "jurisdiction":"harbor", "opened_at":"2026-02-20", "shelter_type":"clinic",
            "residents":50, "staff_count":2, "medical_dependent":True, "pet_count":0, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"B", "generator_tested_at":"2026-02-18",
            "transport_status":"pending", "water_days":3, "accessibility_retrofit":False, "exceptions":[]
        },
    ]
    for case in generated:
        (work / f"{case['case_id']}.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n")

    out = tmp_path / "out"
    got = read_outputs(build_and_run(work, out))
    exp = expected_for(work)
    assert got == exp
    decisions = {d["case_id"]: d for d in got[1]["decisions"]}

    assert decisions["pet-matrix-901-no-pets"]["decision"] == "approve"
    assert "PET_HARBOR_ADVISORY" not in decisions["pet-matrix-901-no-pets"]["applied_rules"]
    assert decisions["pet-matrix-902-colocated"]["decision"] == "approve"
    assert "PET_HARBOR_ADVISORY" in decisions["pet-matrix-902-colocated"]["applied_rules"]
    assert "PET_HARBOR_ADVISORY" not in decisions["pet-matrix-903-pet-wing"]["applied_rules"]
    assert decisions["pet-matrix-904-mandatory"]["reasons"] == ["PET_ACCOMMODATION_MISSING"]
    assert "PET_HARBOR_ADVISORY" not in decisions["pet-matrix-904-mandatory"]["applied_rules"]
    assert decisions["pet-matrix-905-staff-waiver"]["decision"] == "conditional"
    assert decisions["pet-matrix-905-staff-waiver"]["reasons"] == ["STAFFING_RATIO_TOO_HIGH"]
    assert "PET_HARBOR_ADVISORY" in decisions["pet-matrix-905-staff-waiver"]["applied_rules"]
    assert decisions["pet-matrix-906-med-no-pets"]["reasons"] == ["ACCESSIBILITY_RETROFIT_REQUIRED"]
    assert "PET_HARBOR_ADVISORY" not in decisions["pet-matrix-906-med-no-pets"]["applied_rules"]

def test_generator_recency_edges_are_data_driven(tmp_path):
    """Generated cases verify inclusive generator recency boundaries and future-test rejection."""
    work = tmp_path / "cases"
    shutil.copytree(CASE_ROOT, work)
    generated = [
        {
            "case_id":"gen-recency-907-seven-days", "jurisdiction":"inland", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":30, "staff_count":1, "medical_dependent":False, "pet_count":0, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-13",
            "transport_status":"not_required", "water_days":3, "accessibility_retrofit":True, "exceptions":[]
        },
        {
            "case_id":"gen-recency-908-eight-days", "jurisdiction":"inland", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":30, "staff_count":1, "medical_dependent":False, "pet_count":0, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-12",
            "transport_status":"not_required", "water_days":3, "accessibility_retrofit":True, "exceptions":[{"code":"WATER_SHORTAGE_1D","source":"irrelevant", "expires_on":"2026-02-28"}]
        },
        {
            "case_id":"gen-recency-909-future", "jurisdiction":"inland", "opened_at":"2026-02-20", "shelter_type":"school",
            "residents":30, "staff_count":1, "medical_dependent":False, "pet_count":0, "pet_accommodation":False,
            "evacuation_status":"advisory", "generator_class":"C", "generator_tested_at":"2026-02-21",
            "transport_status":"not_required", "water_days":3, "accessibility_retrofit":True, "exceptions":[]
        },
    ]
    for case in generated:
        (work / f"{case['case_id']}.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n")

    out = tmp_path / "out"
    got = read_outputs(build_and_run(work, out))
    exp = expected_for(work)
    assert got == exp
    decisions = {d["case_id"]: d for d in got[1]["decisions"]}

    assert "GENERATOR_TEST_STALE" not in decisions["gen-recency-907-seven-days"]["reasons"]
    assert decisions["gen-recency-908-eight-days"]["reasons"] == ["GENERATOR_TEST_STALE"]
    assert decisions["gen-recency-908-eight-days"]["decision"] == "deny"
    assert decisions["gen-recency-909-future"]["reasons"] == ["GENERATOR_TEST_STALE"]
