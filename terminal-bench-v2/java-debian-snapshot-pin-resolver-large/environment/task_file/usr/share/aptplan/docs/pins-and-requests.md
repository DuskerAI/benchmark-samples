# Requests, Snapshots, and Preferences

The request file provides `request_id`, `architecture`, `requested`, `held`, and `forbidden_suites`. The plan must satisfy every requested package for the requested architecture. A package listed in `held` must not be selected. A snapshot whose Release file has `Trusted: no` must not contribute selected packages, and packages from suites named in `forbidden_suites` must not be selected.

Preference files contain stanzas with `Package`, optional `Snapshot`, optional `Suite`, and `Pin-Priority`. The most specific matching package rule wins over a wildcard rule. If two matching rules are equally specific, the later rule in lexical file order wins. Candidates with negative priority are ineligible. Otherwise choose the eligible candidate with the highest priority; break ties by Debian version order and then by snapshot name and package name for deterministic output.

The output should be stable across runs. Sort selected package records by package name. Sort dependency name arrays and audit entries by package name, then decision, then reason.
