# Dusker AI expert bench

121 custom Terminal-Bench-style tasks maintained by Dusker AI. Intended GitHub
organization: [`dusker-dynamics`](https://github.com/dusker-dynamics).

Each task contains agent instructions, environment files, metadata, a reference
solution and verifier files. Author: **Dusker AI expert bench** · **tasks@dusker.ai**.

## Version and provenance

All tasks declare `version = "2.0"` in `task.toml` and share the same package layout,
so they are grouped together directly under `terminal-bench-v2/`. This declaration is **not** an official
Terminal-Bench dataset release identifier. These are custom Dusker AI tasks, not a
verified copy of the official Terminal-Bench 2.0 or 2.1 benchmark. There is no basis
to split this collection into those two official releases.

Official Terminal-Bench 2.1 revises tasks from 2.0 to fix dependencies, resource
budgets and instruction/test mismatches; it is not a separate directory format.
See the [official release notes](https://www.tbench.ai/news/terminal-bench-2-1)
and [Harbor task documentation](https://docs.harborframework.com/tasks/overview).

## Repository layout

```text
terminal-bench-v2/
├── README.md
├── catalog.json                 Website-friendly task inventory
├── task-packages.sha256         Content hashes (all files except this manifest)
└── <task-id>/
    ├── README.md
    ├── instruction.md
    ├── task.toml
    ├── environment/
    ├── solution/
    └── tests/
```

## Website integration

[`catalog.json`](catalog.json) contains task IDs, repository-relative links,
categories, languages, difficulty, tags and author metadata. A website can use it
to build searchable/filterable task cards. Repository-relative paths avoid assuming
a repository name or branch before publication.

`benchmark_release` and `evaluation_results` are `null`: benchmark membership and
model scores are not established by this export. No leaderboard, evaluation
transcripts or verified pass rates are included. Do not present missing results
as a score of zero or claim official Terminal-Bench affiliation.

## Integrity verification

From the repository root:

```sh
sha256sum --check task-packages.sha256
```

Hashes establish packaging integrity, not runtime correctness or digital signatures.
Regenerate the manifest after changing packaged files.

## Running a task

Requires Docker and a compatible Harbor installation:

```sh
harbor run -p ada-btrfs-recovery-auditor -a oracle --jobs-dir runs/oracle
harbor run -p ada-btrfs-recovery-auditor -a nop --jobs-dir runs/nop
```

Task-specific READMEs contain corresponding commands. Runtime compatibility and
controls were not revalidated during this packaging operation. Verifiers and
reference solutions must be hidden from the agent during evaluation.

## Publication and licensing

The source samples package carries a proprietary Dusker AI notice. No replacement
license has been selected here. Confirm the intended public distribution terms
and bundled third-party licensing before publishing. Making a repository public
does not by itself grant an open-source license.

## Task inventory

| Task | Category | Languages | Difficulty |
| --- | --- | --- | --- |
| [c-cose-replay-gate](c-cose-replay-gate/) | security | C | hard |
| [cpp-webauthn-admission-gate](cpp-webauthn-admission-gate/) | security | C++, bash | hard |
| [ada-btrfs-recovery-auditor](ada-btrfs-recovery-auditor/) | security | Ada, bash | hard |
| [ada-cr3bp-manifold-continuation](ada-cr3bp-manifold-continuation/) | software-engineering | bash | unknown |
| [ada-ext4-journal-recovery](ada-ext4-journal-recovery/) | system-administration | Ada, bash | hard |
| [ada-isometric-scanline-renderer](ada-isometric-scanline-renderer/) | games | Ada, bash | hard |
| [ada-nft-policy-compiler](ada-nft-policy-compiler/) | security | Ada, bash | hard |
| [ada-roughtime-response-auditor](ada-roughtime-response-auditor/) | security | Ada, bash | hard |
| [ada-tpm-pcr-eventlog-audit](ada-tpm-pcr-eventlog-audit/) | security | Ada, bash | hard |
| [c-allocator-replay](c-allocator-replay/) | data-processing | C | hard |
| [c-ansi-build-log-viewer-ui](c-ansi-build-log-viewer-ui/) | build-and-dependency-management | c, html, javascript | hard |
| [c-dax-inode-policy-engine](c-dax-inode-policy-engine/) | system-administration | C, bash | hard |
| [c-der-chain-admission](c-der-chain-admission/) | security | C, bash | medium |
| [c-fido2-hid-admission-gate](c-fido2-hid-admission-gate/) | security | C, bash | hard |
| [c-forward-secure-log-auditor](c-forward-secure-log-auditor/) | security | C, bash | hard |
| [c-io-uring-replay](c-io-uring-replay/) | data-processing | c | hard |
| [c-tty-packet-replay-ui](c-tty-packet-replay-ui/) | security | c, html, javascript | hard |
| [clojure-kalman-smoother](clojure-kalman-smoother/) | scientific-computing | Clojure, bash | hard |
| [cpp-bgp-mrt-repair](cpp-bgp-mrt-repair/) | data-processing | c++, bash | hard |
| [cpp-cardrail-stateful-emv-replay-large](cpp-cardrail-stateful-emv-replay-large/) | security | cpp, bash | hard |
| [cpp-dwarf-cfi-unwinder](cpp-dwarf-cfi-unwinder/) | data-processing | C++ | hard |
| [cpp-elf-abi-compatibility-auditor-large](cpp-elf-abi-compatibility-auditor-large/) | build-and-dependency-management | c++, bash | hard |
| [cpp-elf-note-summarizer](cpp-elf-note-summarizer/) | data-processing | c++, bash | hard |
| [cpp-hpack-continuation-decoder-repair](cpp-hpack-continuation-decoder-repair/) | security | C++, bash | hard |
| [cpp-hpack-security-audit-analyzer](cpp-hpack-security-audit-analyzer/) | security | C++, bash | hard |
| [cpp-ipfix-flow-template-auditor-large](cpp-ipfix-flow-template-auditor-large/) | data-processing | c++, bash | hard |
| [cpp-kryo-fem-checkpoint](cpp-kryo-fem-checkpoint/) | debugging | C++, bash | hard |
| [cpp-quic-replay-reassembler](cpp-quic-replay-reassembler/) | data-processing | C++, bash | hard |
| [cpp-reloc-planner](cpp-reloc-planner/) | build-and-dependency-management | c++, bash | hard |
| [cpp-sctp-reassembly-state](cpp-sctp-reassembly-state/) | data-processing | c++, bash | hard |
| [cpp-stiff-ode-enclosure](cpp-stiff-ode-enclosure/) | scientific-computing | C++, bash | hard |
| [cpp-tactical-canvas-shader](cpp-tactical-canvas-shader/) | games | C++, bash | hard |
| [crystal-linker-resolver](crystal-linker-resolver/) | build-and-dependency-management | crystal, bash | hard |
| [crystal-quant-inference](crystal-quant-inference/) | machine-learning | Crystal | hard |
| [crystal-riichi-ledger](crystal-riichi-ledger/) | games | Crystal, Bash | hard |
| [crystal-sat-package-lock-auditor](crystal-sat-package-lock-auditor/) | build-and-dependency-management | crystal, bash | hard |
| [d-pseudo-arclength-continuation](d-pseudo-arclength-continuation/) | scientific-computing | d, bash | hard |
| [d-taylor-ode-enclosure](d-taylor-ode-enclosure/) | scientific-computing | D, bash | hard |
| [d864d846-1813-4d1b-8d02-96332218ade6](d864d846-1813-4d1b-8d02-96332218ade6/) | security | OCaml | hard |
| [elixir-backgammon-match-arbiter](elixir-backgammon-match-arbiter/) | games | Elixir, Bash | hard |
| [elixir-beam-telemetry-replay](elixir-beam-telemetry-replay/) | system-administration | Elixir | hard |
| [erlang-mqtt-session-replay](erlang-mqtt-session-replay/) | security | Erlang | hard |
| [erlang-quic-replay-gate](erlang-quic-replay-gate/) | security | Erlang | medium |
| [erlang-rebar-release-resolver-final](erlang-rebar-release-resolver-final/) | build-and-dependency-management | Erlang | hard |
| [erlang-ssh-certificate-policy](erlang-ssh-certificate-policy/) | security | Erlang | hard |
| [erlang-vector-clock-arbiter](erlang-vector-clock-arbiter/) | games | Erlang, bash | hard |
| [fortran-adaptive-quadrature-restart](fortran-adaptive-quadrature-restart/) | scientific-computing | fortran, bash | medium |
| [fortran-amr-reflux-restart-payload-1c758df5](fortran-amr-reflux-restart-payload-1c758df5/) | scientific-computing | fortran, bash | hard |
| [fortran-dg-shallow-water-restart](fortran-dg-shallow-water-restart/) | scientific-computing | fortran, bash | hard |
| [fortran-dqds-eigen-auditor](fortran-dqds-eigen-auditor/) | scientific-computing | fortran, bash | hard |
| [fortran-netcdf-restart-solver](fortran-netcdf-restart-solver/) | scientific-computing | fortran, bash | hard |
| [fortran-orbit-restart](fortran-orbit-restart/) | scientific-computing | fortran, bash | hard |
| [fortran-shallow-water-restart](fortran-shallow-water-restart/) | scientific-computing | Fortran, bash | hard |
| [fsharp-nuget-prerelease-resolver](fsharp-nuget-prerelease-resolver/) | build-and-dependency-management | F#, bash | hard |
| [go-continuity-shelter-policy-auditor-large](go-continuity-shelter-policy-auditor-large/) | system-administration | go, bash | hard |
| [go-ct-log-gate](go-ct-log-gate/) | security | Go, bash | hard |
| [go-edge-gateway](go-edge-gateway/) | system-administration | go, bash | hard |
| [go-jose-gate](go-jose-gate/) | security | Go, bash | hard |
| [go-journal-vacuum-auditor](go-journal-vacuum-auditor/) | system-administration | Go, bash | hard |
| [go-quic-transcript-validator](go-quic-transcript-validator/) | security | go, bash | hard |
| [go-raft-replay-recovery](go-raft-replay-recovery/) | data-processing | Go | medium |
| [go-raft-snapshot-compaction-repair](go-raft-snapshot-compaction-repair/) | data-processing | go, bash | hard |
| [guile-firewall-restore-planner](guile-firewall-restore-planner/) | system-administration | Guile Scheme, bash | medium |
| [guile-sysroot-solver](guile-sysroot-solver/) | build-and-dependency-management | Guile Scheme | hard |
| [haskell-cabal-flag-solver](haskell-cabal-flag-solver/) | build-and-dependency-management | haskell, bash | hard |
| [haskell-cabal-planner](haskell-cabal-planner/) | build-and-dependency-management | Haskell, bash | hard |
| [haskell-lens-calibrator](haskell-lens-calibrator/) | scientific-computing | Haskell, bash | hard |
| [haskell-spectral-checkpoint-solver](haskell-spectral-checkpoint-solver/) | scientific-computing | haskell, bash | hard |
| [java-debian-snapshot-pin-resolver-large](java-debian-snapshot-pin-resolver-large/) | build-and-dependency-management | java, bash | hard |
| [julia-fem-checkpoint-restart](julia-fem-checkpoint-restart/) | scientific-computing | julia, bash | hard |
| [julia-gmres-restart-auditor](julia-gmres-restart-auditor/) | scientific-computing | julia, bash | hard |
| [kotlin-sigstore-bundle-auditor](kotlin-sigstore-bundle-auditor/) | security | kotlin, bash | hard |
| [lua-register-vm-resume](lua-register-vm-resume/) | games | Nim, bash | hard |
| [lua-seccomp-bpf-broker](lua-seccomp-bpf-broker/) | security | lua, bash | hard |
| [lyra-matchroom-host](lyra-matchroom-host/) | system-administration | Bash | hard |
| [nim-amr-certifier-staging-e7e587e7](nim-amr-certifier-staging-e7e587e7/) | scientific-computing | Nim, bash | hard |
| [nim-cose-sign1-audit-final](nim-cose-sign1-audit-final/) | security | nim | hard |
| [nim-nix-store-closure-admission](nim-nix-store-closure-admission/) | build-and-dependency-management | Nim, bash | hard |
| [nim-sensor-bitstream-decoder](nim-sensor-bitstream-decoder/) | scientific-computing | nim, bash | hard |
| [nim-shogi-judge](nim-shogi-judge/) | games | Nim, bash, Python | hard |
| [nim-wireguard-transcript-auditor](nim-wireguard-transcript-auditor/) | security | Nim, bash | medium |
| [nim-x509-path-audit](nim-x509-path-audit/) | security | Nim, bash | hard |
| [ocaml-dnssec-nsec3-validator](ocaml-dnssec-nsec3-validator/) | security | OCaml | hard |
| [ocaml-rpki-roa-auditor](ocaml-rpki-roa-auditor/) | security | OCaml, bash | hard |
| [ocaml-x509-policy-admission](ocaml-x509-policy-admission/) | security | OCaml | hard |
| [prolog-seki-superko-arbiter](prolog-seki-superko-arbiter/) | games | Prolog, bash | hard |
| [quorum-evidence-attest](quorum-evidence-attest/) | security | Erlang, bash | hard |
| [racket-dnssec-denial-audit](racket-dnssec-denial-audit/) | security | Racket | hard |
| [racket-go-superko-arbiter](racket-go-superko-arbiter/) | games | Racket, Bash | medium |
| [racket-go-tournament-arbiter](racket-go-tournament-arbiter/) | games | Racket, Bash | hard |
| [racket-macro-sandbox](racket-macro-sandbox/) | security | Racket, bash | hard |
| [racket-wasm-guard](racket-wasm-guard/) | security | racket, bash | medium |
| [ruby-rpm-admission-planner](ruby-rpm-admission-planner/) | build-and-dependency-management | Ruby, bash | hard |
| [ruby-tpm-measured-boot-auditor](ruby-tpm-measured-boot-auditor/) | security | Ruby, bash | hard |
| [rust-acme-order-gate](rust-acme-order-gate/) | system-administration | Rust, bash | hard |
| [rust-astrometric-plate-calibrator](rust-astrometric-plate-calibrator/) | scientific-computing | rust | hard |
| [rust-decimal-interval-engine](rust-decimal-interval-engine/) | scientific-computing | rust, bash | medium |
| [rust-limited-angle-tomography-repair_submission1](rust-limited-angle-tomography-repair_submission1/) | scientific-computing | rust | medium |
| [rust-nftables-rollback](rust-nftables-rollback/) | system-administration | Rust, bash | hard |
| [rust-nvme-zoned-replay-recovery](rust-nvme-zoned-replay-recovery/) | system-administration | Rust | hard |
| [rust-onnx-int8-replay](rust-onnx-int8-replay/) | machine-learning | Rust | hard |
| [rust-quantized-transformer-cache](rust-quantized-transformer-cache/) | machine-learning | Rust, bash | hard |
| [rust-sparse-solver-checkpoint](rust-sparse-solver-checkpoint/) | scientific-computing | rust, bash | hard |
| [rust-trace-timeline-viewer](rust-trace-timeline-viewer/) | data-processing | rust, typescript, html, css | hard |
| [rust-wal-recovery](rust-wal-recovery/) | security | Rust, bash | hard |
| [rust-wal-recovery-boundary](rust-wal-recovery-boundary/) | data-processing | rust, bash | hard |
| [scala-jvm-stackmap-verifier](scala-jvm-stackmap-verifier/) | security | Scala | hard |
| [scala-sbt-eviction-planner](scala-sbt-eviction-planner/) | build-and-dependency-management | Scala, bash | hard |
| [swift-webauthn-attestation-gate](swift-webauthn-attestation-gate/) | security | Swift, bash | hard |
| [typescript-arena-replay](typescript-arena-replay/) | games | TypeScript, bash | hard |
| [zig-ansi-dungeon-renderer](zig-ansi-dungeon-renderer/) | games | zig, bash | hard |
| [zig-build-manifests](zig-build-manifests/) | build-and-dependency-management | zig, cmake, bash | hard |
| [zig-ebpf-admission-gate](zig-ebpf-admission-gate/) | security | Zig, bash | hard |
| [zig-ebpf-safety-verifier](zig-ebpf-safety-verifier/) | security | Zig, bash | hard |
| [zig-hermetic-codegen-build](zig-hermetic-codegen-build/) | build-and-dependency-management | zig, bash | hard |
| [zig-lightmap-minimap-renderer](zig-lightmap-minimap-renderer/) | games | Zig, bash | hard |
| [zig-minisign-witness](zig-minisign-witness/) | security | Zig, bash | hard |
| [zig-onnx-quant-inference](zig-onnx-quant-inference/) | machine-learning | Zig, bash | hard |
| [zig-retrograde-tablebase](zig-retrograde-tablebase/) | games | zig, bash | medium |
| [zig-riscv-elf-relocation-auditor](zig-riscv-elf-relocation-auditor/) | security | zig, bash | hard |
| [zig-unit-restart-planner](zig-unit-restart-planner/) | system-administration | Zig, bash | hard |
