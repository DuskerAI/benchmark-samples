## The dispatch loop for the register machine.

import isa, state

const
  MaxCallDepth* = 128     ## maximum number of simultaneous call frames
  MaxTailChain* = 10000   ## maximum length of a single tail-call chain
  MaxSteps*     = 20_000_000

type
  VMError* = object of CatchableError

proc reg(vm: var VM, r: int): int {.inline.} =
  vm.stack[vm.frames[^1].base + r]

proc setReg(vm: var VM, r, val: int) {.inline.} =
  vm.stack[vm.frames[^1].base + r] = val

proc ensureWindow(vm: var VM, base, numRegs: int) =
  let need = base + numRegs
  while vm.stack.len < need:
    vm.stack.add 0

proc pushFrame(vm: var VM, cl: Closure, base, retReg: int) =
  if vm.frames.len >= MaxCallDepth:
    raise newException(VMError, "call depth exceeded")
  ensureWindow(vm, base, vm.protos[cl.protoIdx].numRegs)
  vm.frames.add Frame(closure: cl, pc: 0, base: base, retReg: retReg)

proc tailFrame(vm: var VM, cl: Closure, args: seq[int]) =
  ## Replace the current frame with a call to `cl`, reusing its base.
  ## Tail calls do not grow the frame stack but the chain length is bounded.
  inc vm.frames[^1].tailDepth
  if vm.frames[^1].tailDepth >= MaxTailChain:
    raise newException(VMError, "tail-call chain exceeded")
  let base = vm.frames[^1].base
  let retReg = vm.frames[^1].retReg
  let td = vm.frames[^1].tailDepth
  ensureWindow(vm, base, vm.protos[cl.protoIdx].numRegs)
  var saved = args
  for i in 0 ..< saved.len:
    vm.stack[base + i] = saved[i]
  vm.frames[^1] = Frame(closure: cl, pc: 0, base: base,
                        retReg: retReg, tailDepth: td)

proc buildClosure(vm: var VM, fidx: int, env: Closure): Closure =
  ## Create a closure over proto `fidx`, capturing upvalues by name from `env`.
  result = Closure(protoIdx: fidx, upvals: @[])
  for name in vm.protos[fidx].upvalNames:
    var found: Upvalue = nil
    if env != nil:
      for i, un in vm.protos[env.protoIdx].upvalNames:
        if un == name and i < env.upvals.len:
          found = env.upvals[i]
          break
    if found == nil:
      found = newUpvalue(vm, 0)
    result.upvals.add found

proc stepOnce(vm: var VM) =
  ## Execute exactly one instruction of the top frame.
  var fr = addr vm.frames[^1]
  let proto = vm.protos[fr.closure.protoIdx]
  if fr.pc < 0 or fr.pc >= proto.code.len:
    raise newException(VMError, "pc out of range")
  let instr = proto.code[fr.pc]
  let op = opcodeOf(instr)
  inc fr.pc
  inc vm.steps

  case op
  of opHALT:
    vm.result = reg(vm, fieldA(instr))
    vm.halted = true
  of opLOADK:
    setReg(vm, fieldA(instr), fieldB(instr))
  of opMOVE:
    setReg(vm, fieldA(instr), reg(vm, fieldB(instr)))
  of opADD:
    setReg(vm, fieldA(instr), reg(vm, fieldB(instr)) + reg(vm, fieldC(instr)))
  of opSUB:
    setReg(vm, fieldA(instr), reg(vm, fieldB(instr)) - reg(vm, fieldC(instr)))
  of opMUL:
    setReg(vm, fieldA(instr), reg(vm, fieldB(instr)) * reg(vm, fieldC(instr)))
  of opLT:
    setReg(vm, fieldA(instr),
           (if reg(vm, fieldB(instr)) < reg(vm, fieldC(instr)): 1 else: 0))
  of opEQ:
    setReg(vm, fieldA(instr),
           (if reg(vm, fieldB(instr)) == reg(vm, fieldC(instr)): 1 else: 0))
  of opJMP:
    vm.frames[^1].pc += unpackSbx(instr)
  of opTEST:
    if reg(vm, fieldA(instr)) != 0:
      vm.frames[^1].pc += unpackSbx(instr)
  of opCLOSURE:
    let fidx = fieldB(instr)
    let cur = vm.frames[^1].closure
    let cl = buildClosure(vm, fidx, cur)
    vm.closures.add cl
    setReg(vm, fieldA(instr), vm.closures.len - 1)
  of opGETUPVAL:
    let u = fieldB(instr)
    setReg(vm, fieldA(instr), vm.frames[^1].closure.upvals[u].value)
  of opSETUPVAL:
    let u = fieldA(instr)
    vm.frames[^1].closure.upvals[u].value = reg(vm, fieldB(instr))
  of opCALL:
    let a = fieldA(instr)
    let nargs = fieldB(instr)
    let handle = reg(vm, a)
    let cl = vm.closures[handle]
    let curBase = vm.frames[^1].base
    let newBase = curBase + a + 1
    var args: seq[int]
    for i in 0 ..< nargs:
      args.add vm.stack[curBase + a + 1 + i]
    pushFrame(vm, cl, newBase, a)
    for i in 0 ..< args.len:
      vm.stack[newBase + i] = args[i]
  of opTAILCALL:
    let a = fieldA(instr)
    let nargs = fieldB(instr)
    let handle = reg(vm, a)
    let cl = vm.closures[handle]
    let curBase = vm.frames[^1].base
    var args: seq[int]
    for i in 0 ..< nargs:
      args.add vm.stack[curBase + a + 1 + i]
    tailFrame(vm, cl, args)
  of opRETURN:
    let rv = reg(vm, fieldA(instr))
    let retReg = vm.frames[^1].retReg
    discard vm.frames.pop()
    if vm.frames.len == 0:
      vm.result = rv
      vm.halted = true
    else:
      vm.stack[vm.frames[^1].base + retReg] = rv

proc runLoop(vm: var VM, budget: int) =
  ## Execute until halt, until the initial frame returns, or until `budget`
  ## instructions have run (budget < 0 means unbounded).
  let startDepth = vm.frames.len
  let target = if budget < 0: -1 else: vm.steps + budget
  while not vm.halted and vm.frames.len >= startDepth:
    if target >= 0 and vm.steps >= target:
      break
    if vm.steps >= MaxSteps:
      raise newException(VMError, "step limit exceeded")
    stepOnce(vm)

proc initMain(vm: var VM) =
  let cl = buildClosure(vm, 0, nil)
  vm.closures.add cl
  ensureWindow(vm, 0, vm.protos[0].numRegs)
  vm.frames.add Frame(closure: cl, pc: 0, base: 0, retReg: 0, tailDepth: 0)

proc execFresh*(vm: var VM) =
  initMain(vm)
  runLoop(vm, -1)

proc execCheckpoint*(vm: var VM, limit: int) =
  ## Run at most `limit` instructions from a fresh start, then stop so the
  ## caller can snapshot mid-execution.
  initMain(vm)
  runLoop(vm, limit)

proc resume*(vm: var VM) =
  ## Continue execution from restored snapshot state.
  runLoop(vm, -1)
