# Output Format

The resolver writes two files in the directory supplied by `--out`.

Package metadata is read from the exact `Packages` file in each snapshot directory under `/var/lib/aptmirror/snapshots`. Similarly named files, catalog index notes, and sidecar files are not package metadata.

`install_plan.json` is a JSON object with exactly these top-level fields: `request_id`, `architecture`, `selected`, `rejected`, and `summary`.

Each object in `selected` has `package`, `version`, `architecture`, `source_snapshot`, `reason`, and `dependencies`. The `reason` value is `requested` when the package name appears directly in the request's `requested` list, and `dependency` for every selected package brought in transitively. The `dependencies` array lists the real selected package names that satisfy that package's direct dependency groups; virtual dependency names should be recorded as the selected provider package name.

Each object in `rejected` has `package`, `version`, `architecture`, `source_snapshot`, and `reason`.

`summary` has `selected_count`, `rejected_count`, `snapshot_count`, and `has_conflicts`. `selected_count` is the number of objects in `selected`. `rejected_count` is the number of objects in `rejected`. `snapshot_count` is the total number of distinct snapshot names present across all packages loaded from the exact `Packages` files, regardless of whether packages from those snapshots were selected. `has_conflicts` is true only when the selected plan contains a disallowed selected package pair; a correct plan for this request should make it false.

`audit.ndjson` is newline-delimited JSON. Each non-empty line is an object with `package`, `decision`, `reason`, and `details`. It must include decision records for selected and rejected package candidates.
