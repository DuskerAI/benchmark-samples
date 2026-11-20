# go-raft-snapshot-compaction-repair

**Collection:** Terminal-Bench v2-style tasks (task format version 2.0)
**Category:** data-processing · **Difficulty:** hard · **Languages:** go, bash
**Author:** Dusker AI expert bench · tasks@dusker.ai

## Task package

- [Agent instructions](instruction.md)
- [Metadata and runtime limits](task.toml)
- [Environment](environment/)
- [Reference solution](solution/)
- [Verifier](tests/)

The instruction file and any referenced public contract define the task requirements.
The verifier and reference solution must be hidden from the evaluated agent.

## Reproduction

Requires Docker and Harbor. From the collection directory:

```sh
harbor run -p go-raft-snapshot-compaction-repair -a oracle --jobs-dir runs/go-raft-snapshot-compaction-repair/oracle
harbor run -p go-raft-snapshot-compaction-repair -a nop --jobs-dir runs/go-raft-snapshot-compaction-repair/nop
```

No evaluation run records or pass rates are included in this export. These commands
have not been run as part of packaging. Task format version 2.0 does not establish
official benchmark membership or distinguish benchmark v2 from v2.1.
