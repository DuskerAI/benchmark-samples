# erlang-vector-clock-arbiter

**Collection:** Terminal-Bench v2-style tasks (task format version 2.0)
**Category:** games · **Difficulty:** hard · **Languages:** Erlang, bash
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
harbor run -p erlang-vector-clock-arbiter -a oracle --jobs-dir runs/erlang-vector-clock-arbiter/oracle
harbor run -p erlang-vector-clock-arbiter -a nop --jobs-dir runs/erlang-vector-clock-arbiter/nop
```

No evaluation run records or pass rates are included in this export. These commands
have not been run as part of packaging. Task format version 2.0 does not establish
official benchmark membership or distinguish benchmark v2 from v2.1.
