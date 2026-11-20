## Loader for the textual program format (.rasm).
##
## A program is a list of function prototypes.  Each proto starts with a
## header line and is followed by its instructions, one per line:
##
##   .proto <name> regs=<n> upvals=<comma-list-or-none>
##   LOADK  0 7
##   ...
##
## Labels end with ':' on their own line and may be used as jump targets;
## JMP/TEST take a label and the loader resolves it to a signed displacement
## relative to the instruction *after* the branch.

import std/[strutils, tables]
import isa, state

proc parseReg(s: string): int =
  if s.startsWith("R") or s.startsWith("r"):
    parseInt(s[1 .. ^1])
  else:
    parseInt(s)

proc loadProgram*(text: string): seq[Proto] =
  var
    protos: seq[Proto] = @[]
    cur: Proto
    haveProto = false
    labels = initTable[string, int]()
    pending: seq[tuple[idx: int, op: OpCode, a: int, label: string]]
    protoNames = initTable[string, int]()

  proc flush() =
    if haveProto:
      for p in pending:
        if not labels.hasKey(p.label):
          raise newException(ValueError, "unknown label: " & p.label)
        let disp = labels[p.label] - (p.idx + 1)
        cur.code[p.idx] = packSbx(p.op, p.a, disp)
      protos.add cur
    labels = initTable[string, int]()
    pending = @[]

  for rawLine in text.splitLines():
    var line = rawLine
    let hashPos = line.find('#')
    if hashPos >= 0:
      line = line[0 ..< hashPos]
    line = line.strip()
    if line.len == 0:
      continue

    if line.startsWith(".proto"):
      flush()
      cur = Proto(code: @[], numRegs: 0, upvalNames: @[])
      haveProto = true
      let parts = line.splitWhitespace()
      let name = parts[1]
      protoNames[name] = protos.len
      for p in parts[2 .. ^1]:
        if p.startsWith("regs="):
          cur.numRegs = parseInt(p[5 .. ^1])
        elif p.startsWith("upvals="):
          let rest = p[7 .. ^1]
          if rest != "none":
            for u in rest.split(','):
              cur.upvalNames.add u.strip()
      continue

    if line.endsWith(":"):
      labels[line[0 ..< ^1].strip()] = cur.code.len
      continue

    let parts = line.splitWhitespace()
    let mnem = parts[0].toUpperAscii()
    case mnem
    of "HALT":
      cur.code.add packAbc(opHALT, parseReg(parts[1]), 0, 0)
    of "LOADK":
      cur.code.add packAbc(opLOADK, parseReg(parts[1]), parseInt(parts[2]), 0)
    of "MOVE":
      cur.code.add packAbc(opMOVE, parseReg(parts[1]), parseReg(parts[2]), 0)
    of "ADD":
      cur.code.add packAbc(opADD, parseReg(parts[1]), parseReg(parts[2]), parseReg(parts[3]))
    of "SUB":
      cur.code.add packAbc(opSUB, parseReg(parts[1]), parseReg(parts[2]), parseReg(parts[3]))
    of "MUL":
      cur.code.add packAbc(opMUL, parseReg(parts[1]), parseReg(parts[2]), parseReg(parts[3]))
    of "LT":
      cur.code.add packAbc(opLT, parseReg(parts[1]), parseReg(parts[2]), parseReg(parts[3]))
    of "EQ":
      cur.code.add packAbc(opEQ, parseReg(parts[1]), parseReg(parts[2]), parseReg(parts[3]))
    of "JMP":
      pending.add (cur.code.len, opJMP, 0, parts[1])
      cur.code.add 0'u32
    of "TEST":
      pending.add (cur.code.len, opTEST, parseReg(parts[1]), parts[2])
      cur.code.add 0'u32
    of "CLOSURE":
      cur.code.add packAbc(opCLOSURE, parseReg(parts[1]), parseInt(parts[2]), 0)
    of "GETUPVAL":
      cur.code.add packAbc(opGETUPVAL, parseReg(parts[1]), parseInt(parts[2]), 0)
    of "SETUPVAL":
      cur.code.add packAbc(opSETUPVAL, parseInt(parts[1]), parseReg(parts[2]), 0)
    of "CALL":
      cur.code.add packAbc(opCALL, parseReg(parts[1]), parseInt(parts[2]), 0)
    of "TAILCALL":
      cur.code.add packAbc(opTAILCALL, parseReg(parts[1]), parseInt(parts[2]), 0)
    of "RETURN":
      cur.code.add packAbc(opRETURN, parseReg(parts[1]), 0, 0)
    else:
      raise newException(ValueError, "unknown mnemonic: " & mnem)

  flush()
  result = protos
