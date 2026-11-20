# nim-amr-certifier-staging-e7e587e7

**Collection:** Terminal-Bench v2-style tasks (task format version 2.0)
**Category:** scientific-computing · **Difficulty:** hard · **Languages:** Nim, bash
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
harbor run -p nim-amr-certifier-staging-e7e587e7 -a oracle --jobs-dir runs/nim-amr-certifier-staging-e7e587e7/oracle
harbor run -p nim-amr-certifier-staging-e7e587e7 -a nop --jobs-dir runs/nim-amr-certifier-staging-e7e587e7/nop
```

No evaluation run records or pass rates are included in this export. These commands
have not been run as part of packaging. Task format version 2.0 does not establish
official benchmark membership or distinguish benchmark v2 from v2.1.
