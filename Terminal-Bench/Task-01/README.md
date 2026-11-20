# Task 01 — SBOM Vulnerability & Compliance Analyzer

**Suite:** Terminal-Bench · **Source task ID:** `03-sbom-vulnerability-analyzer` · **Category:** security · **Language:** Go
**Difficulty:** hard · **Expert / junior estimate:** 120 / 900 min · **Verifier:** 88 pytest assertions

| Model (pass@4) | Passes | Solved |
| --- | :---: | :---: |
| Claude Opus 4.8 | **3 / 4** | Yes |
| GPT-5.5 | **0 / 4** | No |

---

## Objective

The agent writes a Go program (`/app/task_file/analyzer.go`) that cross-references three
CycloneDX SBOMs against a vulnerability database and emits a policy-compliance report
(`output_data/report.json`). It is a realistic software-supply-chain security workflow:
what a platform-security team runs before a release gate.

| Input | Content |
| --- | --- |
| `sboms/webplatform.cdx.json` | 27 components, 28 dependency edges, criticality `critical` (npm) |
| `sboms/api-gateway.cdx.json` | 20 components, 21 dependency edges, criticality `high` (Go modules) |
| `sboms/analytics-pipeline.cdx.json` | 20 components, 21 dependency edges, criticality `medium` (PyPI) |
| `vulndb.json` | 41 advisories: CVSS 3.1 vector strings (no scores), affected packages, version ranges, fixed versions |
| `policies.json` | Per-criticality severity budgets (`-1` = unlimited) and CVE exemptions |

The full prompt is in [`03-sbom-vulnerability-analyzer/instruction.md`](03-sbom-vulnerability-analyzer/instruction.md).

## Technical challenges

1. **CVSS 3.1 base-score computation from first principles.** The database supplies only vector
   strings, with no scores or labels. The program must parse each vector (`AV/AC/PR/UI/S/C/I/A`), apply
   the scope-dependent Privileges-Required weights, compute impact/exploitability, and apply
   the specification's round-up function. Scope-changed vectors are a common source of
   off-by-0.1 errors.
2. **Version-range matching.** Constraints such as `>=1.2.3 <2.0.0` and `<3.0.0` must be
   evaluated numerically per `major.minor.patch`, per ecosystem (npm / Go / PyPI). The vulndb
   contains near-miss advisories that *must not* match (11 dedicated negative tests).
3. **Dependency-graph reasoning.** Transitive findings need `direct: false` plus the
   **shortest** dependency chain from the project root (BFS over the CycloneDX `dependencies`
   graph). Some vulnerable packages sit three or more hops deep.
4. **Policy evaluation with exemptions.** Exempted CVEs are reported but excluded from the
   severity counts that drive `policy_violations` and `compliant`.
5. **Exact output contract.** The report has nested sorting rules (projects by name, findings by
   vulnerability ID), one-decimal CVSS floats, and a fixed key layout.

## Environment

| Item | Value |
| --- | --- |
| Base image | `ubuntu:22.04` (`linux/amd64`) |
| Toolchain | Go 1.21.13, Python 3 (verifier only) |
| Resources | 1 CPU · 2 GB RAM · 5 GB storage |
| Timeouts | agent 1200 s · verifier 900 s · build 600 s |

See [`environment/Dockerfile`](03-sbom-vulnerability-analyzer/environment/Dockerfile) and
[`task.toml`](03-sbom-vulnerability-analyzer/task.toml).

## Verification methodology

[`tests/test.sh`](03-sbom-vulnerability-analyzer/tests/test.sh) runs
[`tests/test_outputs.py`](03-sbom-vulnerability-analyzer/tests/test_outputs.py) (88 tests). Reward is
binary: all tests must pass.

| Test group | Tests | What it checks |
| --- | :---: | --- |
| `TestStructure` | 8 | Go source and binary exist, report is valid JSON, projects sorted, **no hard-coded scores**, **input files unmodified** |
| `Test<Project>Meta` × 3 | 15 | Name, criticality, compliance verdict, component and vulnerable-component counts |
| `Test<Project>Findings` × 3 | 28 | Per-advisory findings: score, severity, fixed version, `direct`, exact shortest chain, exemption flag |
| `Test<Project>Summary` × 3 | 18 | Severity counts (non-exempt), exempt count, `policy_violations` |
| `TestNonMatchingCVEs` | 11 | Advisories whose version range or ecosystem must **not** match |
| `TestCVSSComputation` | 8 | Independent checks of computed scores (9.8, 9.1, 8.8, 8.2, 7.4, 6.1 scope-changed, 5.4, 4.3) |

Anti-shortcut design: the expected scores are the specification-correct values for each vector. A
heuristic check fails the run if five or more of those score literals appear in the Go source, and
SHA-256 hashes guard every input file.

## Evaluation results

Run configuration (details in [`../README.md`](../README.md#evaluation-protocol)): pass@4 per model,
native agent harness, Docker. GPT-5.5 used high reasoning effort. The Opus 4.8 attempts on this task
used the harness default effort.

| Model | Trial | Reward | Tests passed | Failing tests |
| --- | --- | :---: | :---: | --- |
| Claude Opus 4.8 | [`T7EjkEu`](evaluation/claude-opus-4.8/pass4-batch-03-05-v2/03-sbom-vulnerability-analyzer__T7EjkEu/verifier/test-stdout.txt) | 1 | 88 / 88 | — |
| Claude Opus 4.8 | [`bu7BNyb`](evaluation/claude-opus-4.8/pass4-batch-03-05-v2/03-sbom-vulnerability-analyzer__bu7BNyb/verifier/test-stdout.txt) | 0 | 85 / 88 | `test_policy_violations` × 3 projects |
| Claude Opus 4.8 | [`dmTjNg3`](evaluation/claude-opus-4.8/pass4-03-topup/03-sbom-vulnerability-analyzer__dmTjNg3/verifier/test-stdout.txt) | 1 | 88 / 88 | — |
| Claude Opus 4.8 | [`5zvFTA9`](evaluation/claude-opus-4.8/pass4-03-topup/03-sbom-vulnerability-analyzer__5zvFTA9/verifier/test-stdout.txt) | 1 | 88 / 88 | — |
| GPT-5.5 | [`TKbirXT`](evaluation/gpt-5.5/03-sbom-vulnerability-analyzer__TKbirXT/verifier/test-stdout.txt) | 0 | 85 / 88 | `test_policy_violations` × 3 projects |
| GPT-5.5 | [`kLASmRk`](evaluation/gpt-5.5/03-sbom-vulnerability-analyzer__kLASmRk/verifier/test-stdout.txt) | 0 | 85 / 88 | `test_policy_violations` × 3 projects |
| GPT-5.5 | [`udTNHaf`](evaluation/gpt-5.5/03-sbom-vulnerability-analyzer__udTNHaf/verifier/test-stdout.txt) | 0 | 85 / 88 | `test_policy_violations` × 3 projects |
| GPT-5.5 | [`vkvTbXF`](evaluation/gpt-5.5/03-sbom-vulnerability-analyzer__vkvTbXF/verifier/test-stdout.txt) | 0 | 85 / 88 | `test_policy_violations` × 3 projects |

**Pooled:** 3 / 8 trials passed (37.5%).

### Observed failure mode

Every failing trial produced correct CVSS scores, version matches, transitive chains and
severity counts. All 85 of those assertions passed. The failures came from one place: where
`policy_violations` sits in the report. The instruction lists it among the contents of `summary`
("`summary` with counts …, `exempted` count, and `policy_violations`"), and the verifier reads
`summary.policy_violations`. The GPT-5.5 trajectories declare it as a project-level field
alongside `summary` (e.g., `PolicyViolations []string \`json:"policy_violations"\`` in the project
struct), which raises `KeyError: 'policy_violations'` in all three summary tests. The task therefore
separates models on **precise schema adherence in a long, dense specification** rather than on the
security computation itself.

## Reproduce

```sh
# From this directory, with Harbor and Docker installed
harbor run -p 03-sbom-vulnerability-analyzer -a oracle   # reference solution → expected reward 1
harbor run -p 03-sbom-vulnerability-analyzer -a nop      # no changes        → expected reward 0
```

The reference solution is [`solution/solve.sh`](03-sbom-vulnerability-analyzer/solution/solve.sh).
It writes a self-contained Go analyzer (the script is ≈600 lines), then builds and runs it.

## Contents

```text
Task-01/
├── 03-sbom-vulnerability-analyzer/   Runnable task package (unchanged from source)
│   ├── instruction.md · task.toml
│   ├── environment/                  Dockerfile + input SBOMs, vulndb, policies
│   ├── solution/solve.sh             Reference (oracle) solution
│   └── tests/                        test.sh + test_outputs.py (verifier)
└── evaluation/
    ├── claude-opus-4.8/<job>/<trial>/   agent/trajectory.json · verifier/{test-stdout.txt,reward.txt}
    └── gpt-5.5/<trial>/                 agent/trajectory.json · verifier/{test-stdout.txt,reward.txt}
```
