# zig-onnx-quant-inference

**Collection:** Terminal-Bench v2-style tasks (task format version 2.0)
**Category:** machine-learning · **Difficulty:** hard · **Languages:** Zig, bash
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
harbor run -p zig-onnx-quant-inference -a oracle --jobs-dir runs/zig-onnx-quant-inference/oracle
harbor run -p zig-onnx-quant-inference -a nop --jobs-dir runs/zig-onnx-quant-inference/nop
```

No evaluation run records or pass rates are included in this export. These commands
have not been run as part of packaging. Task format version 2.0 does not establish
official benchmark membership or distinguish benchmark v2 from v2.1.
