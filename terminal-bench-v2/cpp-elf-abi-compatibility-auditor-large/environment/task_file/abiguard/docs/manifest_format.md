# Baseline manifest format

The `.abi` files are line-oriented to keep the release gate auditable without network services or external package managers.

Supported records:

- `library <filename>` names the release shared object to inspect.
- `soname <dynamic-soname>` records the ABI-stable SONAME expected in `.dynamic`.
- `export <symbol> <version> <default|nondefault> [allow]` records a public export. The optional `allow` marker means the policy may downgrade removal to `ALLOWLISTED_BREAK`.
- `version_node <version>` records a version definition that must remain present.
- `need_version <needed-soname> <version>` records the maximum dependency version the baseline release required.

Blank lines and `#` comments are ignored.
