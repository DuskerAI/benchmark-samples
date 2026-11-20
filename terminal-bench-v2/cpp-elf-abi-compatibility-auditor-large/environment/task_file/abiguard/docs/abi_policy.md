# ABI policy contract

`abiguard` compares each baseline manifest in `/var/lib/abiguard/baselines` with the corresponding shared object in the release directory. The manifest lists the expected dynamic SONAME, public exports, version nodes, and dependency version requirements for one logical library.

An export is identified by the triple `(symbol name, version name, default flag)`. A non-default symbol version is still public and must not be collapsed with the default version. Undefined symbols are not public exports. Weak undefined symbols are optional references and must not appear as exported API.

Report these issue codes when applicable: `REMOVED_EXPORT`, `DEFAULT_VERSION_CHANGED`, `MISSING_VERSION_NODE`, `SONAME_BREAK`, `NEW_REQUIRED_DEP_VERSION`, `UNDOCUMENTED_GLOBAL`, and `ALLOWLISTED_BREAK`. Allowlisted removed exports still appear in the trace, but they are not blocking compatibility failures.

`DEFAULT_VERSION_CHANGED` is emitted when a baseline default export is absent, but the release still exports the same symbol name with the same default/nondefault mode under a different version. The trace row's `version` field must contain the replacement version observed in the release ELF, not the baseline version. For example, if the baseline has `alpha_open ALPHA_1.0 default` and the release has `alpha_open@@ALPHA_2.0`, emit `DEFAULT_VERSION_CHANGED` with `symbol: "alpha_open"` and `version: "ALPHA_2.0"`.

Do not also emit `UNDOCUMENTED_GLOBAL` for a release export that was already accounted for as a version replacement of a baseline symbol with the same default/nondefault mode. `UNDOCUMENTED_GLOBAL` is only for exported symbols whose `(symbol name, default flag)` pair does not appear in the baseline manifest at all.

`abi_report.json` must contain exactly the top-level keys `summary` and `libraries`. `soname_matrix.csv` must use the header `library,expected_soname,actual_soname,status`; write one row per manifest using `status` value `ok` when the observed SONAME matches the manifest and `break` otherwise. `risk_trace.jsonl` contains one JSON object per issue with at least `library`, `code`, `symbol`, `version`, and `blocking`; use an empty string for `symbol` or `version` only when the issue has no meaningful value for that field.

Trace field conventions are part of the output contract. For `SONAME_BREAK`, set `symbol` to `""` and set `version` to the observed release SONAME. For `NEW_REQUIRED_DEP_VERSION`, set `symbol` to the needed SONAME filename and set `version` to the newly required dependency version string. For `MISSING_VERSION_NODE`, set `symbol` to `""` and `version` to the missing node. For `REMOVED_EXPORT`, `ALLOWLISTED_BREAK`, `DEFAULT_VERSION_CHANGED`, and `UNDOCUMENTED_GLOBAL`, set `symbol` to the affected export name and `version` to the baseline, replacement, or observed export version named by the issue.

The CLI must exit with status `0` after successfully writing the reports, even when blocking compatibility failures are present. Reserve nonzero exit codes for usage errors or cases where reports cannot be produced.

Output order must be deterministic: sort libraries by manifest name and issues by library, code, symbol, and version.
