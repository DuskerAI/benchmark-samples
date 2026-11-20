# Public release review

## Scope and findings

The initial publication was reviewed on 2026-11-20 using Gitleaks 8.30.1,
including archive traversal, plus checks for credential files, private-key
headers, credential assignments, personal paths and unexpected large files.
The histories of the four distinct bundled upstream Git repositories were also
scanned. Local audit reports and temporary repositories are not included here.

No unredacted live credentials or private keys were identified by these checks.
The scanner's 133 initial working-tree alerts were reviewed as:

- 111 source-file checksums in verifier inventories;
- 20 occurrences of a JavaScript method name in bundled upstream code;
- two occurrences of a public GPG signing-key fingerprint in recorded environment output.

The bundled Git histories produced one alert for the same JavaScript method.
Archive traversal produced no additional alerts. `.gitleaks.toml` allows only
the exact reviewed non-secret values, not whole files or directories.
Four local Python bytecode cache files were excluded from the Git publication
and checksum manifests; their local copies were left untouched.

Recorded API-key assignments found in the supplied logs use publication
placeholders or are error-message text, not usable keys. Public author contacts,
synthetic security-test keys/addresses, public upstream attribution and upstream
developer paths remain where they are part of the task or historical evidence.
Such test fixtures are not deployment credentials.

These checks are not a guarantee that every kind of sensitive information has
been detected, a malware audit, a runtime validation, or a legal review. Git
bundles retain upstream commit history and public author metadata. Packaged
agent runs are historical records; they may include internal container paths,
session identifiers, prompts and commands. Consumers should review them before
republishing or reusing them.

## Intended public material

Tasks, hidden verifiers, reference solutions and packaged evaluation evidence
are intentionally public in this samples repository. During an actual evaluation,
do not expose the verifier or reference solution to the agent. Publication can
cause benchmark contamination; results should identify the task revision and
whether the tested model could have encountered this public material.

The existing proprietary [LICENSE](LICENSE) is unchanged. Public availability
does not grant an open-source license. Upstream components retain their own
licenses; see [SWE notices](swe-bench/NOTICE.md) and bundled component metadata.

## Checking changes

```sh
gitleaks dir . --config .gitleaks.toml --max-archive-depth 3 --redact
python3 -B verify_samples.py
```

Never include raw secret-scan reports or private credentials in commits or issue
reports. Report suspected exposure privately to **tasks@dusker.ai**; do not paste
the suspected value in a public GitHub issue.
