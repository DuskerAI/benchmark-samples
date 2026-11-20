> **Sample-package note.** This guide is copied from the full Dusker SWE repository. Paths such as
> `dataset10/luajit-virtual-heap-exits` refer to that layout; in this sample the same task is
> `luajit-virtual-heap-exits` (Task-01 = dataset10, Task-02 = dataset9, Task-03 = dataset8,
> Task-04 = dataset6). `tools/validate.py` and `catalog.json` are not included here; use
> `../../verify_samples.py` instead. `tools/archive.py` is included and works from `swe-bench/`.

# Evaluation

## Local validation

The repository was organized with Harbor 0.22.0 installed on macOS and a Linux
ARM64 Docker environment. Use the existing Harbor and Docker installations:

```sh
harbor --version
docker info
python3 -B tools/validate.py --bundles --harbor
```

The Python check verifies package hashes, job indexes, native result hashes,
archive references, and recorded control results. `--bundles` also reconstructs
each baseline and checks both patches without executing them. `--harbor` resolves
each local task through the installed CLI. These checks make no model calls and
do not replace a Docker build or execution of acceptance tests.

## Oracle and NOP

Run from the repository root. New output goes under ignored `runs/`, keeping
published evidence intact:

```sh
env -u DOCKER_DEFAULT_PLATFORM harbor run \
  -p dataset10/luajit-virtual-heap-exits -a oracle \
  -n 1 -k 1 -r 0 --jobs-dir runs/oracle

env -u DOCKER_DEFAULT_PLATFORM harbor run \
  -p dataset10/luajit-virtual-heap-exits -a nop \
  -n 1 -k 1 -r 0 --jobs-dir runs/nop
```

Substitute a catalog task path to evaluate another task. Oracle applies the
reference fix. NOP keeps the buggy baseline. An expected NOP failure is a zero
reward from the intended behavioral assertions, with no infrastructure exception.
Docker errors, unsupported network policies, verifier crashes, or missing results
are infrastructure failures. Fix the provider configuration before evaluating a
model; do not weaken a task's declared policy to hide an environment error.

A reference clean regrade reconstructs the pinned baseline in a fresh environment,
applies the reference fix, and runs the unchanged verifier. It checks independence
from stale containers, prior workspaces, and cached verdicts. Historical Oracle,
NOP, and clean-regrade results are linked in each catalog entry.

## Model trials

Use a bounded agent timeout, an explicit model and agent version, fixed effort,
and no automatic retries. Run trials sequentially. Start with one trial and inspect
its grade, transcript, duration, and cost before extending the series. Provider
credentials belong in the local environment and must not be committed.

Report full-task pass rates alongside individual test failures. Separate acceptance
failures, candidate build failures, timeouts, and infrastructure failures. A single
missed requirement can be a legitimate failure, but does not establish broad or
extreme difficulty. Four trials on one model provide limited evidence of general
model capability.

Historical records retain the model IDs, commands, paths, task checksums, and cost
estimates observed at execution time. Absolute paths in those records are historical
provenance; current portable paths are in `jobs/index.json`. Provider billing was
not independently measured. No new model trial was run during repository packaging.

## Archived duplicate captures

`*.reference.json` represents a byte-identical capture retained elsewhere in the
same dataset. Verify or materialize it from the repository root:

```sh
python3 -B tools/archive.py verify PATH_TO_MISSING_DIRECTORY
python3 -B tools/archive.py restore PATH_TO_MISSING_DIRECTORY
```

Restoration verifies every file hash and refuses to overwrite an existing path.
Use original jobs for regrading when possible. Restoring an optional duplicate
changes the archived tree; remove that restored copy before checking the published
evidence hash again.
