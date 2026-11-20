# Output schema

The auditor writes exactly three files into the output directory supplied on the command line. JSON output must be UTF-8, deterministic, and pretty printed with two-space indentation. CSV output must use a header row and LF line endings.

`effective_rules.json` is an object with `schema_version` equal to `1`, `policy_documents` equal to the number of Markdown files found below the policy root, and `rules`, an array sorted by `id`. Each rule has `id`, `value`, `source`, and `priority`. The canonical rule records are exactly these values:

| id | value | source | priority |
| --- | --- | --- | --- |
| `ACCESS_DELTA_LARGE` | `delta shelters over 80 residents require accessibility retrofit` | `annexes/delta-accessibility.md` | `70` |
| `GEN_BASE` | `minimum generator class C` | `handbook/operations-01.md` | `10` |
| `GEN_MEDICAL` | `medical dependency requires generator class B` | `amendments/2025-11-medical-power.md` | `50` |
| `GEN_RECENCY` | `generator test must be within seven days before opening` | `bulletins/generator-recency.md` | `72` |
| `GEN_RIDGE_MANDATORY` | `ridge mandatory evacuation requires generator class A` | `errata/2026-01-ridge-generator.md` | `90` |
| `PET_HARBOR_ADVISORY` | `harbor advisory cases may colocate pets without a pet wing` | `memos/harbor-advisory-pets.md` | `80` |
| `PET_STANDARD` | `pet cases require pet accommodation` | `handbook/operations-07.md` | `10` |
| `STAFFING` | `default 32 residents per staff, 26 medical, 24 mandatory` | `amendments/2025-12-staffing.md` | `60` |
| `TRANSPORT_MANDATORY` | `mandatory evacuation requires confirmed transport unless narrow pending exception applies` | `annexes/transport-window.md` | `65` |
| `WATER_DEFAULT` | `minimum three days water reserve` | `handbook/operations-03.md` | `10` |
| `WATER_ISLAND` | `island jurisdiction requires five days water reserve` | `amendments/2026-02-island-water.md` | `75` |

`case_decisions.json` is an object with `schema_version` equal to `1`, `case_count`, and `decisions`, sorted by `case_id`. Each decision has `case_id`, `jurisdiction`, `decision`, `reasons`, and `applied_rules`. The decision is `approve` when there are no unresolved violations, `conditional` when every violation is covered by a valid narrow exception, and `deny` otherwise. `reasons` and `applied_rules` are sorted arrays of strings.

The only valid reason strings are `GENERATOR_CLASS_TOO_LOW`, `GENERATOR_TEST_STALE`, `WATER_RESERVE_TOO_LOW`, `STAFFING_RATIO_TOO_HIGH`, `PET_ACCOMMODATION_MISSING`, `TRANSPORT_NOT_CONFIRMED`, and `ACCESSIBILITY_RETROFIT_REQUIRED`. When the narrow pending transport allowance documented in the corpus applies, `TRANSPORT_NOT_CONFIRMED` is omitted from `reasons` entirely.

The standing rule identifiers `GEN_BASE`, `GEN_RECENCY`, `WATER_DEFAULT`, `STAFFING`, and `PET_STANDARD` are included in `applied_rules` for every case because every case is evaluated against those default rules. Include conditional rule identifiers only when their exact scope is active: `GEN_MEDICAL` for medical dependency, `GEN_RIDGE_MANDATORY` for ridge mandatory evacuation, `WATER_ISLAND` for island cases opened on or after 2026-02-01, `PET_HARBOR_ADVISORY` only for harbor advisory cases with `pet_count` greater than zero and `pet_accommodation` false, `TRANSPORT_MANDATORY` for mandatory evacuation, and `ACCESS_DELTA_LARGE` only for delta jurisdiction cases with more than 80 residents. Medical dependency can produce `ACCESSIBILITY_RETROFIT_REQUIRED`, but it does not add `ACCESS_DELTA_LARGE`.

`exceptions.csv` has columns `case_id,exception_code,source,expires_on,accepted`. It contains one row for every exception offered by a case file, sorted by `case_id` and then `exception_code`. `accepted` is `true` only when the exception is still active for the case open date and its exact exception code covers a violation within the narrow tolerance defined by the corpus.
