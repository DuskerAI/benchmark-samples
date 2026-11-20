#!/bin/bash
set -euo pipefail

cd /app

cat > /app/cabal-workspace/cabal.project <<'EOF'
packages:
  packages/*
  apps/*
  tools/*

active-repositories: :none

constraints:
  acme-codec +fast,
  acme-engine +release,
  acme-attestation +local-kms +reproducible,
  acme-manifest +signed-metadata,
  acme-policy +strict +signed

optimization: True
tests: False
benchmarks: False
EOF

python3 - <<'PY'
from pathlib import Path

api = Path("/app/cabal-workspace/packages/api/acme-api.cabal")
api.write_text(api.read_text().replace("version: 2.0.0", "version: 2.1.0").replace("Acme.Api.Internal", "Acme.Api"))

engine = Path("/app/cabal-workspace/packages/engine/acme-engine.cabal")
engine.write_text(engine.read_text().replace("acme-api >=2.2 && <3", "acme-api ^>=2.1"))

app = Path("/app/cabal-workspace/apps/plan-report/plan-report.cabal")
app.write_text(app.read_text().replace("acme-engine:core", "acme-engine ^>=3.2"))

schema = Path("/app/cabal-workspace/packages/schema/acme-schema.cabal")
schema.write_text(
    schema.read_text()
    .replace("cabal-version: 2.4", "cabal-version: 3.0")
    .replace("version: 0.8.0", "version: 0.9.0")
    .replace("Acme.Schema.Internal", "Acme.Schema")
    .replace("Acme.Schema.Wire", "Acme.Schema.Fingerprint")
    .replace("library fingerprint\n", "library fingerprint\n  visibility: public\n")
    .replace("hs-source-dirs: internal", "hs-source-dirs: src-internal")
    .replace("acme-api >=2.2 && <3", "acme-api ^>=2.1")
)

policy = Path("/app/cabal-workspace/packages/policy/acme-policy.cabal")
policy.write_text(policy.read_text().replace("acme-schema >=1.0 && <2", "acme-schema ^>=0.9"))

manifest = Path("/app/cabal-workspace/packages/manifest/acme-manifest.cabal")
manifest.write_text(
    manifest.read_text()
    .replace("cabal-version: 2.4", "cabal-version: 3.0")
    .replace("version: 5.1.0", "version: 5.2.0")
    .replace("Acme.Manifest.Internal", "Acme.Manifest")
    .replace("library fingerprint\n", "library fingerprint\n  visibility: public\n")
    .replace("Acme.Manifest.Wire", "Acme.Manifest.Fingerprint")
    .replace("hs-source-dirs: routes", "hs-source-dirs: src-fingerprint")
    .replace("acme-schema:wire", "acme-schema:fingerprint")
    .replace("acme-schema >=1.0 && <2", "acme-schema ^>=0.9")
)

attestation = Path("/app/cabal-workspace/packages/attestation/acme-attestation.cabal")
attestation.write_text(
    attestation.read_text()
    .replace("cabal-version: 2.4", "cabal-version: 3.0")
    .replace("version: 0.3.0", "version: 0.4.0")
    .replace("Acme.Attestation.Internal", "Acme.Attestation")
    .replace("acme-manifest >=5.3 && <6", "acme-manifest ^>=5.2")
    .replace("acme-policy >=4.1 && <5", "acme-policy ^>=4.0")
    .replace("library token\n", "library token\n  visibility: public\n")
    .replace("Acme.Attestation.Receipt", "Acme.Attestation.Token")
    .replace("hs-source-dirs: token", "hs-source-dirs: src-token")
    .replace("acme-manifest:wire", "acme-manifest:fingerprint")
)

probe = Path("/app/cabal-workspace/tools/workspace-probe/workspace-probe.cabal")
probe.write_text(
    probe.read_text()
    .replace("cabal-version: 2.4", "cabal-version: 3.0")
    .replace("main-is: Probe.hs", "main-is: Main.hs")
    .replace("acme-schema:wire", "acme-schema:fingerprint")
    .replace("acme-policy >=4.1 && <5", "acme-policy ^>=4.0")
)

gate = Path("/app/cabal-workspace/tools/release-gate/release-gate.cabal")
gate.write_text(
    gate.read_text()
    .replace("cabal-version: 2.4", "cabal-version: 3.0")
    .replace("main-is: Gate.hs", "main-is: Main.hs")
    .replace("acme-attestation >=0.5 && <1", "acme-attestation ^>=0.4")
    .replace("acme-attestation:receipt", "acme-attestation:token")
    .replace("acme-manifest >=5.3 && <6", "acme-manifest ^>=5.2")
    .replace("acme-manifest:wire", "acme-manifest:fingerprint")
    .replace("acme-policy >=4.1 && <5", "acme-policy ^>=4.0")
)
PY

cd /app/cabal-workspace
cabal build all --offline
