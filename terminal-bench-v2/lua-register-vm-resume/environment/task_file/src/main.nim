## Command-line driver for the register machine.
##
## Usage:
##   rvm run       <program.rasm> <result.json>
##   rvm checkpoint <program.rasm> <steps> <snapshot.json> <result.json>
##   rvm resume    <program.rasm> <snapshot.json> <result.json>

import std/[os, json, tables, strutils]
import isa, state, loader, interp

proc newVM(protos: seq[Proto]): VM =
  result = VM(protos: protos, stack: @[], frames: @[], closures: @[],
              upvalPool: initTable[int, Upvalue](), nextUpvalId: 0,
              halted: false, result: 0, steps: 0)

proc writeResult(path: string, vm: VM, status: string) =
  var o = newJObject()
  o["status"] = %status
  o["result"] = %vm.result
  o["halted"] = %vm.halted
  o["steps"] = %vm.steps
  writeFile(path, pretty(o) & "\n")

proc writeError(path: string, msg: string) =
  var o = newJObject()
  o["status"] = %"error"
  o["reason"] = %msg
  writeFile(path, pretty(o) & "\n")

proc main() =
  let args = commandLineParams()
  if args.len < 1:
    quit("missing command", 1)
  let cmd = args[0]

  case cmd
  of "run":
    if args.len != 3: quit("usage: run <program> <result>", 1)
    let protos = loadProgram(readFile(args[1]))
    var vm = newVM(protos)
    try:
      execFresh(vm)
      writeResult(args[2], vm, "ok")
    except VMError as e:
      writeError(args[2], e.msg)

  of "checkpoint":
    if args.len != 5: quit("usage: checkpoint <program> <steps> <snapshot> <result>", 1)
    let protos = loadProgram(readFile(args[1]))
    let steps = parseInt(args[2])
    var vm = newVM(protos)
    try:
      execCheckpoint(vm, steps)
      writeFile(args[3], pretty(toSnapshot(vm)) & "\n")
      writeResult(args[4], vm, (if vm.halted: "ok" else: "suspended"))
    except VMError as e:
      writeError(args[4], e.msg)

  of "resume":
    if args.len != 4: quit("usage: resume <program> <snapshot> <result>", 1)
    let protos = loadProgram(readFile(args[1]))
    var vm = newVM(protos)
    let snap = parseJson(readFile(args[2]))
    fromSnapshot(vm, snap)
    try:
      resume(vm)
      writeResult(args[3], vm, "ok")
    except VMError as e:
      writeError(args[3], e.msg)

  else:
    quit("unknown command: " & cmd, 1)

main()
