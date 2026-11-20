# Package Relationship Rules

`Depends` is a comma-separated list. Each comma group must be satisfied. Alternatives are separated with `|`; satisfying any one alternative satisfies that group. Architecture qualifiers such as `[amd64]` mean the alternative applies only for that architecture, and `[!amd64]` means it applies except for that architecture.

A dependency may be satisfied by a selected package with the same name or by a selected package whose `Provides` field names the requested virtual package. Selected dependency names in `install_plan.json` should be the real selected package names, not the virtual names.

No selected package may appear with another selected package if either package names the other in `Conflicts` or `Breaks`. `Replaces` is informational for this task and does not override `Conflicts` or `Breaks`.
