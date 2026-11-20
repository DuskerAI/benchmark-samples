## Runtime state: closures, call frames, upvalues, and the snapshot format
## used by checkpoint/resume.

import std/[json, tables]
import isa

type
  Proto* = object
    ## A compiled function prototype.
    code*: seq[Instr]
    numRegs*: int          ## register window size
    upvalNames*: seq[string]   ## names of captured upvalues, in order

  Upvalue* = ref object
    ## A mutable captured value shared between closures.
    id*: int
    value*: int

  Closure* = ref object
    protoIdx*: int
    upvals*: seq[Upvalue]

  Frame* = object
    closure*: Closure
    pc*: int
    base*: int             ## index of R[0] for this frame in the value stack
    retReg*: int           ## caller register receiving the return value
    tailDepth*: int        ## length of the current tail-call chain

  VM* = object
    protos*: seq[Proto]
    stack*: seq[int]       ## flat register/value stack
    frames*: seq[Frame]
    closures*: seq[Closure]  ## live closures, indexed by handle
    upvalPool*: Table[int, Upvalue]  ## id -> live upvalue
    nextUpvalId*: int
    halted*: bool
    result*: int
    steps*: int            ## instructions executed (for determinism/limits)

proc newUpvalue*(vm: var VM, value: int): Upvalue =
  result = Upvalue(id: vm.nextUpvalId, value: value)
  vm.upvalPool[result.id] = result
  inc vm.nextUpvalId

# ---------------------------------------------------------------------------
# Snapshot serialization.
#
# A snapshot captures enough to resume execution deterministically: the whole
# value stack, every frame (closure proto, pc, base, return register, and the
# ids of the upvalues its closure references), and the pool of live upvalues
# keyed by id so shared upvalues reconnect on restore.
# ---------------------------------------------------------------------------

proc toSnapshot*(vm: VM): JsonNode =
  result = newJObject()
  result["steps"] = %vm.steps
  result["nextUpvalId"] = %vm.nextUpvalId
  result["stack"] = %vm.stack

  var pool = newJArray()
  for id, uv in vm.upvalPool:
    var o = newJObject()
    o["id"] = %uv.id
    o["value"] = %uv.value
    pool.add o
  result["upvals"] = pool

  var closures = newJArray()
  for cl in vm.closures:
    var o = newJObject()
    o["proto"] = %cl.protoIdx
    var ids = newJArray()
    for uv in cl.upvals:
      ids.add %uv.id
    o["upvals"] = ids
    closures.add o
  result["closures"] = closures

  var frames = newJArray()
  for fr in vm.frames:
    var o = newJObject()
    o["proto"] = %fr.closure.protoIdx
    o["pc"] = %fr.pc
    o["base"] = %fr.base
    o["retReg"] = %fr.retReg
    o["tailDepth"] = %fr.tailDepth
    var ids = newJArray()
    for uv in fr.closure.upvals:
      ids.add %uv.id
    o["upvals"] = ids
    frames.add o
  result["frames"] = frames

proc fromSnapshot*(vm: var VM, node: JsonNode) =
  ## Rebuild live VM state from a snapshot produced by `toSnapshot`.
  vm.steps = node["steps"].getInt
  vm.nextUpvalId = node["nextUpvalId"].getInt
  vm.halted = false

  vm.stack = @[]
  for v in node["stack"]:
    vm.stack.add v.getInt

  vm.upvalPool = initTable[int, Upvalue]()
  for o in node["upvals"]:
    let uv = Upvalue(id: o["id"].getInt, value: o["value"].getInt)
    vm.upvalPool[uv.id] = uv

  vm.closures = @[]
  for o in node["closures"]:
    var cl = Closure(protoIdx: o["proto"].getInt, upvals: @[])
    for idn in o["upvals"]:
      let uid = idn.getInt
      let fresh = Upvalue(id: uid, value: 0)
      cl.upvals.add fresh
    vm.closures.add cl

  vm.frames = @[]
  for o in node["frames"]:
    var cl = Closure(protoIdx: o["proto"].getInt, upvals: @[])
    for idn in o["upvals"]:
      let uid = idn.getInt
      if vm.upvalPool.hasKey(uid):
        cl.upvals.add vm.upvalPool[uid]
    let td = if o.hasKey("tailDepth"): o["tailDepth"].getInt else: 0
    var fr = Frame(closure: cl, pc: o["pc"].getInt,
                   base: o["base"].getInt, retReg: o["retReg"].getInt,
                   tailDepth: td)
    vm.frames.add fr
