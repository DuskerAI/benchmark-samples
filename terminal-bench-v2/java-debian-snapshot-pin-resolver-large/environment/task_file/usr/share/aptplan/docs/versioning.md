# Local Debian Version Rules

Versions use the Debian shape `[epoch:]upstream[-revision]`. A missing epoch is `0` and is lower than any positive epoch. Compare epoch numerically first. If epochs are equal, compare upstream and then revision using Debian ordering: digit runs compare numerically, non-digit runs compare lexically, and `~` sorts before the empty string and before every other character. A missing revision is lower than a present revision after upstream is equal.

Version constraints in dependency fields use `>=`, `>`, `=`, `<=`, or `<` inside parentheses. The constraint applies to real package candidates with the named package. Constraints on virtual package names are satisfied by any provider listed in the candidate's `Provides` field unless the provider entry itself declares a version.
