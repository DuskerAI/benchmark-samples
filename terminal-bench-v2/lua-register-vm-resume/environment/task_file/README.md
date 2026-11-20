# Puzzle game rulebook

This is the rulebook for a small terminal puzzle game. Each level ships as a
compiled level file, and the game plays a level to its end, or saves it after a
fixed number of played steps and continues it later from a saved game so a
player can pause a level and pick it up without replaying it. The game keeps its
working values in a row of numbered slots as it plays a level out.

## Command line

`/app/bin/rvm` accepts three commands and always writes a JSON result file:

- `run <program.rasm> <result.json>` runs a program from a fresh state.
- `checkpoint <program.rasm> <steps> <snapshot.json> <result.json>` runs at
  most `<steps>` instructions from a fresh state, writes the live state to
  `<snapshot.json>`, and writes the result file.
- `resume <program.rasm> <snapshot.json> <result.json>` rebuilds state from a
  snapshot and continues to completion.

The result JSON has `status`, `result`, `halted`, and `steps`. `status` is
`ok` when execution reached `HALT` or the initial frame returned, `suspended`
when a checkpoint stopped before completion, and `error` with a `reason`
string when execution faulted. `steps` counts instructions executed. Running
a program fresh to completion and running it via any checkpoint/resume split
must produce the same `result`, `halted`, and final `steps`.

## Program format (`.rasm`)

A program is one or more function prototypes. A prototype begins with

```
.proto <name> regs=<n> upvals=<names-or-none>
```

where `regs` is the register-window size and `upvals` is `none` or a
comma-separated list of captured upvalue names in order. Instruction lines
follow, one per line; `#` starts a comment; a bare `label:` line marks a jump
target. Prototype 0 is the entry point and is entered with no arguments.

Registers are written `R0`, `R1`, … (or bare integers). Instructions:

- `LOADK Ra K` — set `Ra` to integer constant `K` (0..511).
- `MOVE Ra Rb` — `Ra = Rb`.
- `ADD/SUB/MUL Ra Rb Rc` — arithmetic on registers.
- `LT/EQ Ra Rb Rc` — `Ra` becomes 1 if the comparison holds, else 0.
- `JMP label` — unconditional branch.
- `TEST Ra label` — branch when `Ra` is non-zero.
- `CLOSURE Ra F` — create a closure over prototype index `F`, capturing its
  named upvalues from the current function's environment, and store a handle
  to it in `Ra`.
- `GETUPVAL Ra U` / `SETUPVAL U Ra` — read/write upvalue slot `U`.
- `CALL Ra N` — call the closure whose handle is in `Ra` with the `N`
  arguments in `Ra+1 … Ra+N`; the return value lands in `Ra`.
- `TAILCALL Ra N` — like `CALL` but reuses the current frame instead of
  growing the call stack.
- `RETURN Ra` — return `Ra` to the caller.
- `HALT Ra` — stop; `Ra` is the program result.

## Encoding

Instructions are packed 32-bit words. Register/count fields are unsigned.
Branch instructions store a signed displacement relative to the instruction
after the branch; a `label:` is assembled into that displacement, which may be
negative for a backward branch.

## Semantics and limits

- Upvalues are shared mutable cells. Two closures that capture the same
  upvalue name from the same environment observe each other's writes, and a
  write made before a checkpoint is visible after a resume.
- A `CALL` opens a new register window above the caller's; a `RETURN` closes
  it and delivers the value to the caller's target register. A `TAILCALL`
  keeps the frame count unchanged.
- A chain of tail calls may reach a length of 10000 without faulting; only a
  chain that would exceed that length faults with a tail-call error.
- The simultaneous call-frame count is capped at 128.
- A snapshot must capture the value stack, every live call frame, every live
  closure and its captured upvalues, and the shared upvalue cells, so that a
  resumed run is indistinguishable from an uninterrupted one.
